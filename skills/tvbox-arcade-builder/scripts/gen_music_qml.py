"""Generate a QtMultimedia Music.qml component resilient to RockFFPlayer stalls.

Plays one track at a time from a Fisher-Yates shuffled full-deck queue, loops
continuously, pauses on app background, and uses a 2s watchdog timer so playback
never silently halts between tracks.
"""
import argparse
import json
import os


def normalize_extensions(raw: str) -> set[str]:
    return {("." + e.strip().lower().lstrip(".")) for e in raw.split(",") if e.strip()}


def main():
    parser = argparse.ArgumentParser(description="Generate single-track-at-a-time Music.qml for Pegasus themes.")
    parser.add_argument("--music-dir", required=True, help="Local directory containing audio files")
    parser.add_argument(
        "--android-music-uri",
        required=True,
        help="Base URI on device (e.g., file:///storage/<UUID>/music/)",
    )
    parser.add_argument("--out", required=True, help="Output Music.qml path")
    parser.add_argument("--volume", type=float, default=0.5, help="Playback volume 0.0-1.0 (default: 0.5)")
    parser.add_argument("--extensions", default=".ogg,.mp3", help="Comma-separated audio extensions (e.g., ogg,mp3)")
    args = parser.parse_args()

    if not os.path.isdir(args.music_dir):
        raise SystemExit(f"Music directory not found: {args.music_dir}")

    exts = normalize_extensions(args.extensions)
    files = sorted(
        f for f in os.listdir(args.music_dir)
        if os.path.isfile(os.path.join(args.music_dir, f)) and os.path.splitext(f)[1].lower() in exts
    )
    if not files:
        raise SystemExit(f"No audio files matching {sorted(exts)} found in {args.music_dir}")

    base_uri = args.android_music_uri if args.android_music_uri.endswith("/") else args.android_music_uri + "/"
    tracks_json = json.dumps(files, ensure_ascii=False)
    vol = max(0.0, min(1.0, args.volume))

    qml = f"""import QtQuick 2.15
import QtMultimedia 5.9

Item {{
    property bool playBGM: true
    property var tracks: {tracks_json}
    property var queue: []
    property int queuePos: 0
    property bool pausedByApp: false

    function buildShuffledQueue() {{
        var arr = [];
        for (var i = 0; i < tracks.length; i++) arr.push(i);
        for (var j = arr.length - 1; j > 0; j--) {{
            var k = Math.floor(Math.random() * (j + 1));
            var tmp = arr[j];
            arr[j] = arr[k];
            arr[k] = tmp;
        }}
        queue = arr;
        queuePos = 0;
    }}

    function playNext() {{
        if (!playBGM || tracks.length === 0) return;
        if (queue.length === 0 || queuePos >= queue.length) {{
            buildShuffledQueue();
        }}
        var idx = queue[queuePos];
        queuePos++;
        bgMusic.stop();
        bgMusic.source = {json.dumps(base_uri)} + encodeURIComponent(tracks[idx]);
        bgMusic.play();
    }}

    function loud() {{ bgMusic.volume = {vol}; }}
    function quiet() {{ bgMusic.volume = {round(vol * 0.2, 2)}; }}
    property bool isPlaying: bgMusic.playbackState === Audio.PlayingState

    Connections {{
        target: Qt.application;
        function onStateChanged() {{
            if (!playBGM) return;
            if (Qt.application.state === Qt.ApplicationActive) {{
                pausedByApp = false;
                if (!isPlaying) {{
                    if (bgMusic.source == "") nextTimer.restart();
                    else bgMusic.play();
                }}
            }} else {{
                pausedByApp = true;
                if (isPlaying) bgMusic.pause();
            }}
        }}
    }}

    Audio {{
        id: bgMusic;
        volume: {vol};
        onStatusChanged: {{
            if (status === Audio.EndOfMedia) {{
                nextTimer.restart();
            }}
        }}
        onError: {{
            if (playBGM && tracks.length > 1) {{
                nextTimer.restart();
            }}
        }}
        Behavior on volume {{
            NumberAnimation {{ duration: 250; easing.type: Easing.InOutQuad; }}
        }}
    }}

    Timer {{
        id: nextTimer;
        interval: 250;
        repeat: false;
        onTriggered: playNext();
    }}

    Timer {{
        id: bgMusicTimer;
        interval: 800;
        repeat: false;
        running: true;
        onTriggered: {{
            if (playBGM && !isPlaying) playNext();
        }}
    }}

    Timer {{
        id: watchdogTimer;
        interval: 2000;
        repeat: true;
        running: true;
        onTriggered: {{
            if (playBGM && !pausedByApp && Qt.application.state === Qt.ApplicationActive) {{
                if (bgMusic.playbackState === Audio.StoppedState && !nextTimer.running) {{
                    nextTimer.restart();
                }}
            }}
        }}
    }}
}}
"""
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(qml)
    print(f"OK: wrote {args.out} with {len(files)} tracks")


if __name__ == "__main__":
    main()
