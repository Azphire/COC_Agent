"""Observe two real player windows and reconnect the second during a live attempt."""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import httpx
from check_character_creation import ROOT, SmokeCheck, port_free, wait_for
from check_multiplayer import BrowserPage

INSTALL = r"""(() => {
  if (window.batch50Evidence) return 'already observing';
  const evidence = window.batch50Evidence = {started:Date.now(), frames:[], dom:[]};
  const NativeSocket = window.WebSocket;
  window.WebSocket = class extends NativeSocket {
    constructor(...args) {
      super(...args);
      this.addEventListener('message', event => {
        try { evidence.frames.push({at:Date.now(), ...JSON.parse(event.data)}); } catch {}
      });
      for (const kind of ['open','close']) this.addEventListener(kind, () =>
        evidence.frames.push({at:Date.now(), type:'observer.socket.' + kind}));
    }
  };
  let previous = '';
  const sample = () => {
    const prose = node => node.querySelector(':scope > p.preserve-lines')?.textContent ?? null;
    const value = {
      status:document.querySelector('[data-testid="agent-cycle-status"]')?.textContent,
      streams:[...document.querySelectorAll('[data-testid="keeper-stream"]')].map(node => ({
        cycle:node.dataset.cycleId, stream:node.dataset.streamId,
        index:node.dataset.streamIndex, attempt:node.dataset.streamAttempt,
        status:node.dataset.streamStatus, text:prose(node)
      })),
      timeline:[...document.querySelectorAll('[data-testid="timeline"] [data-event-seq]')]
        .map(node => ({seq:node.dataset.eventSeq, cycle:node.dataset.cycleId,
          role:node.dataset.actorType, text:prose(node)}))
    };
    const serialized = JSON.stringify(value);
    if (serialized !== previous) {
      previous = serialized; evidence.dom.push({at:Date.now(), ...value});
    }
  };
  new MutationObserver(sample).observe(document.body, {
    subtree:true, childList:true, characterData:true, attributes:true
  });
  sample();
  return 'observing exact visible body text';
})()"""


class Observe(SmokeCheck):
    def __init__(self, directory):
        self.run_directory = directory
        self.directory = directory / "browser-observer"
        self.directory.mkdir(parents=True, exist_ok=False)
        self.processes, self.logs, self.pages = [], [], []
        self.http = httpx.Client(trust_env=False, timeout=10)
        self.frontend_url = "http://127.0.0.1:5250"

    def run(self):
        port_free(5250)
        os.environ["COC_BACKEND_PORT"], os.environ["COC_FRONTEND_PORT"] = "8150", "5250"
        self.start([shutil.which("node"), str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
                    "--host", "127.0.0.1"], ROOT / "frontend", "browser-frontend")
        wait_for(lambda: self.http.get(self.frontend_url).status_code == 200)
        setup = json.loads((self.run_directory / "setup-state.json").read_text(encoding="utf-8"))
        room_id, token = setup["room_id"], setup["player_token"]
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
        print("Two actual player browsers ready; exact body observation installed.", flush=True)
        while not (self.run_directory / "browser-observer-stop").exists():
            for index, page in enumerate(self.pages):
                if index == 1:
                    page.evaluate("""(() => {
                      const frames = window.batch50Evidence.frames;
                      window.reconnectedStreams ||= [];
                      const frame = frames.find(f => f.type === 'keeper.stream.delta'
                        && !window.reconnectedStreams.includes(f.data.stream_id));
                      if (!frame) return;
                      const active = [...document.querySelectorAll('[data-testid="keeper-stream"]')]
                        .find(node => node.dataset.streamId === frame.data.stream_id
                          && node.dataset.streamStatus === 'responding');
                      if (!active) return;
                      window.reconnectedStreams.push(frame.data.stream_id);
                      frames.push({at:Date.now(), type:'observer.forced_reconnect', data:{
                        stream_id:frame.data.stream_id, cycle_id:frame.data.cycle_id,
                        attempt:frame.data.attempt, responding_at_disconnect:true}});
                      window.roomSockets.forEach(s => s.close());
                    })()""")
                result = page.evaluate("""(() => {
                  const source = window.batch50Evidence;
                  const snapshots = source.frames.filter(f => f.type === 'room.snapshot');
                  const frames = source.frames.filter(f =>
                    f.type.startsWith('keeper.stream.') || f.type.startsWith('observer.')
                    || f.type === 'room.event' || f.type === 'room.snapshot').map(f =>
                    f.type !== 'room.snapshot' ? f : {...f, data:{
                      id:f.data.id, is_host:f.data.is_host,
                      self_member_id:f.data.self_member_id, latest_seq:f.data.latest_seq}});
                  return {at:Date.now(), evidence:{...source, frames}, privacy:{
                    snapshot_count:snapshots.length,
                    all_player_projection:snapshots.every(f => f.data.is_host === false)},
                    url:location.href};
                })()""")
                (page.directory / "observation.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
                )
            time.sleep(0.15)
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
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    assert directory.is_relative_to((ROOT / "data/prepared/batch-50").resolve())
    observer = Observe(directory)
    try:
        observer.run()
    finally:
        observer.close_all()
