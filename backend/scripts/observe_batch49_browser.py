"""Two passive player browsers; reconnect one once during each public stream."""

import json
import os
import shutil
import sys
import time

import httpx
from check_character_creation import ROOT, SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage
from observe_batch48 import INSTALL


class Observe(SmokeCheck):
    def __init__(self, directory):
        self.run_directory = directory
        self.directory = directory / (sys.argv[2] if len(sys.argv) > 2 else "browser-observer")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.processes, self.logs, self.pages = [], [], []
        self.http = httpx.Client(trust_env=False, timeout=10)
        self.frontend_url = "http://127.0.0.1:5249"

    def run(self):
        port_free(5249)
        os.environ["COC_BACKEND_PORT"], os.environ["COC_FRONTEND_PORT"] = "8149", "5249"
        self.start([shutil.which("node"), str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                    "--host", "127.0.0.1"], ROOT / "frontend", "browser-frontend")
        wait_for(lambda: self.http.get(self.frontend_url).status_code == 200)
        setup = json.loads((self.run_directory / "setup-state.json").read_text(encoding="utf-8"))
        room_id = setup["room_id"]
        token = setup["player_token"]
        for name in ("player-live", "player-reconnect"):
            page = BrowserPage(self, name)
            page.cdp.protocol.max_size = 64 * 1024 * 1024
            self.pages.append(page)
            page.evaluate(f"localStorage.setItem({json.dumps('coc.room.' + room_id)},"
                          f"{json.dumps(token)})")
            page.navigate(f"#/rooms/{room_id}")
            wait_for(page.connected)
            page.evaluate(INSTALL)
            page.evaluate("window.roomSockets.forEach(s => s.close())")
            wait_for(page.connected)
            page.screenshot("observer-ready.png")
        (self.directory / "browser-observer-ready.json").write_text(json.dumps({
            "room_id": room_id, "browsers": 2, "at": time.time(), "mode": "player-only",
            "processes": [{"name": name, "pid": process.pid} for name, process in self.processes],
        }), encoding="utf-8")
        print("Two player browsers observing; second reconnects per public stream.", flush=True)
        while not (self.run_directory / "browser-observer-stop").exists():
            for index, page in enumerate(self.pages):
                if index == 1:
                    page.evaluate("""(() => {
                      const evidence = window.batch48Evidence;
                      const frames = evidence.frames;
                      window.reconnectedStreams ||= [];
                      const frame = frames.find(f => f.type === 'keeper.stream.delta'
                        && !window.reconnectedStreams.includes(f.data.stream_id));
                      if (frame) {
                        window.reconnectedStreams.push(frame.data.stream_id);
                        frames.push({at:Date.now(), type:'observer.forced_reconnect',
                          data:{stream_id:frame.data.stream_id, cycle_id:frame.data.cycle_id}});
                        window.roomSockets.forEach(s => s.close());
                      }
                    })()""")
                result = page.evaluate("""(() => {
                  const source = window.batch48Evidence;
                  const snapshots = source.frames.filter(f => f.type === 'room.snapshot');
                  const frames = source.frames.filter(f =>
                    f.type.startsWith('keeper.stream.') || f.type.startsWith('observer.')
                    || f.type === 'room.event' || f.type === 'room.snapshot').map(f =>
                    f.type !== 'room.snapshot' ? f : {...f, data:{
                      id:f.data.id, is_host:f.data.is_host,
                      self_member_id:f.data.self_member_id, latest_seq:f.data.latest_seq,
                    }});
                  return {at:Date.now(), evidence:{...source, frames},
                    privacy:{snapshot_count:snapshots.length,
                      all_player_projection:snapshots.every(f => f.data.is_host === false)},
                    text:document.body.innerText, url:location.href};
                })()""")
                (page.directory / "observation.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
                )
            time.sleep(0.4)
        for page in self.pages:
            page.screenshot("final.png")

    def close_all(self):
        for page in self.pages:
            if page.cdp:
                page.cdp.close()
        for _, process in reversed(self.processes):
            self.stop(process)
        self.http.close()
        for log in self.logs:
            log.close()


if __name__ == "__main__":
    observer = Observe(ROOT / "data/prepared/batch-49" / sys.argv[1])
    try:
        observer.run()
    finally:
        observer.close_all()
