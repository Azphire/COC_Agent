"""Passive browser evidence plus ordinary UI actions for the actual short game."""

import argparse
import json
import time

from validate_batch48 import connect_browser

INSTALL = r"""(() => {
  if (window.batch48Evidence) return 'already observing';
  const evidence = window.batch48Evidence = {
    started: Date.now(), frames: [], dom: [], requests: []
  };
  const NativeSocket = window.WebSocket;
  window.WebSocket = class extends NativeSocket {
    constructor(...args) {
      super(...args);
      this.addEventListener('message', event => {
        try {
          const message = JSON.parse(event.data);
          evidence.frames.push({at: Date.now(), ...message});
        } catch { /* Non-JSON frames are not application events. */ }
      });
      this.addEventListener('open', () => evidence.frames.push({
        at: Date.now(), type:'observer.socket.open'
      }));
      this.addEventListener('close', () => evidence.frames.push({
        at: Date.now(), type:'observer.socket.close'
      }));
    }
  };
  const nativeFetch = window.fetch;
  window.fetch = function(resource, ...args) {
    evidence.requests.push({
      at: Date.now(), url: typeof resource === 'string' ? resource : resource.url
    });
    return nativeFetch.call(this, resource, ...args);
  };
  let previous = '';
  const sample = () => {
    const value = {
      status: document.querySelector('[data-testid="agent-cycle-status"]')?.textContent,
      streams: [...document.querySelectorAll('[data-testid="keeper-stream"]')].map(node => ({
        cycle: node.dataset.cycleId, stream: node.dataset.streamId,
        index: node.dataset.streamIndex, attempt: node.dataset.streamAttempt,
        status: node.dataset.streamStatus, text: node.textContent,
      })),
      timeline: Array.from(document.querySelectorAll(
        '[data-testid="timeline"] [data-event-seq]'
      ), node => ({
        seq: node.dataset.eventSeq, cycle: node.dataset.cycleId, text: node.textContent,
      })),
    };
    const serialized = JSON.stringify(value);
    if (serialized !== previous) {
      previous = serialized; evidence.dom.push({at: Date.now(), ...value});
    }
  };
  new MutationObserver(sample).observe(document.body, {
    subtree:true, childList:true, characterData:true, attributes:true
  });
  sample();
  return 'observing ordinary player view';
})()"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--action")
    parser.add_argument("--watch", type=float, default=0)
    args = parser.parse_args()
    browser = connect_browser("host")
    target = browser.directory / f"{args.label}.json"
    if target.exists():
        raise ValueError("Refusing to overwrite original browser evidence")
    try:
        if args.install:
            print(browser.evaluate(INSTALL))
        if args.action:
            browser.fill("#agent-action", args.action)
            sent = time.time()
            browser.click("发送")
            with (browser.directory / "operations.jsonl").open("a", encoding="utf-8") as log:
                log.write(
                    json.dumps(
                        {"at": sent, "fill": "#agent-action", "value": args.action},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                log.write(
                    json.dumps({"at": time.time(), "click": "发送"}, ensure_ascii=False) + "\n"
                )
        deadline = time.monotonic() + min(args.watch, 45)
        while time.monotonic() < deadline:
            browser.evaluate(
                "document.querySelector('[data-testid=agent-cycle-status]')?.textContent"
            )
            time.sleep(0.5)
        result = browser.evaluate(
            "({at:Date.now(), evidence:window.batch48Evidence, "
            "text:document.body.innerText, url:location.href})"
        )
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(result["text"])
    finally:
        browser.cdp.close()
        browser.http.close()


if __name__ == "__main__":
    main()
