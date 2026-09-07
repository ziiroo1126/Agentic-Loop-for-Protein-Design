#!/usr/bin/env python3
"""Exercise the shipped gallery, demo and WebGL viewer using Firefox's Marionette API."""

import argparse
import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path


class Browser:
    def __init__(self, connection):
        self.connection = connection
        self.sequence = 0
        self.read()

    def read(self):
        length = b""
        while not length.endswith(b":"):
            chunk = self.connection.recv(1)
            if not chunk:
                raise RuntimeError("Firefox disconnected")
            length += chunk
        size, value = int(length[:-1]), b""
        while len(value) < size:
            chunk = self.connection.recv(size - len(value))
            if not chunk:
                raise RuntimeError("Firefox disconnected")
            value += chunk
        return json.loads(value)

    def command(self, name, args=None):
        self.sequence += 1
        payload = json.dumps([0, self.sequence, name, args or {}]).encode()
        self.connection.sendall(str(len(payload)).encode() + b":" + payload)
        response = self.read()
        if response[2]:
            raise RuntimeError(response[2])
        return response[3]

    def js(self, script):
        return self.command("WebDriver:ExecuteScript", {"script": script, "args": []})["value"]

    def navigate(self, url):
        self.command("WebDriver:Navigate", {"url": url})

    def click(self, selector):
        # An iframe can extend below its parent's viewport. Scroll all ancestors
        # before a trusted click so the pointer reaches the visible control.
        self.js(
            f"document.querySelector({json.dumps(selector)}).scrollIntoView({{block:'center'}});"
        )
        result = self.command("WebDriver:FindElement", {"using": "css selector", "value": selector})
        element = result.get("value", result)
        identifier = element["element-6066-11e4-a52e-4f735466cecf"]
        self.command("WebDriver:ElementClick", {"id": identifier})

    def wait(self, expression):
        for _ in range(100):
            if self.js("return " + expression + ";"):
                return
            time.sleep(0.1)
        raise AssertionError(f"Browser condition was not met: {expression}")

    def screenshot(self, path):
        result = self.command("WebDriver:TakeScreenshot", {"full": False})
        path.write_bytes(base64.b64decode(result["value"] if isinstance(result, dict) else result))


def exercise(browser, site, output):
    browser.command("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}})
    browser.command("WebDriver:SetWindowRect", {"width": 1440, "height": 1080})
    browser.navigate((site / "index.html").as_uri())
    assert browser.js("return document.querySelectorAll('.cards article').length;") == 3
    assert browser.js("return document.querySelector('.preview img').naturalWidth;") > 500
    browser.screenshot(output / "gallery.png")
    browser.navigate((site / "demo/index.html").as_uri())
    assert browser.js("return document.querySelectorAll('#issues li').length;") == 4
    browser.click("#show-task")
    assert not browser.js("return document.getElementById('task-details').hidden;")
    browser.click('[data-panel="pipeline"]')
    assert browser.js("return document.getElementById('evaluated-count').textContent;") == "0"
    browser.click("#next")
    assert browser.js("return document.getElementById('evaluated-count').textContent;") == "1"
    assert browser.js(
        "return document.getElementById('loop-evidence').textContent.includes('pre.');"
    )
    assert browser.js(
        "return document.getElementById('loop-feedback').textContent.includes('iptm');"
    )
    browser.click("#next")
    assert browser.js(
        "return document.getElementById('loop-stop').textContent.includes('candidate_limit');"
    )
    browser.screenshot(output / "decisions.png")
    browser.click('[data-panel="structure"]')
    browser.command("WebDriver:SwitchToFrame", {"id": 0})
    browser.wait(
        "!!document.getElementById('message') && document.getElementById('message').hidden"
    )
    assert len(browser.js("return document.querySelector('canvas').toDataURL();")) > 20000
    assert browser.js("return document.querySelectorAll('#chains input').length;") == 2
    browser.click("#chains label")
    assert not browser.js("return document.querySelector('#chains input').checked;")
    browser.click("#chains label")
    browser.js(
        "document.getElementById('candidate').value='1';"
        "document.getElementById('candidate').dispatchEvent(new Event('change'));"
    )
    browser.js(
        "document.getElementById('structure').value='predicted_complex';"
        "document.getElementById('structure').dispatchEvent(new Event('change'));"
    )
    browser.wait("document.getElementById('message').hidden")
    assert browser.js("return document.getElementById('atom-count').textContent;") != "—"
    browser.click("#spin")
    assert (
        browser.js("return document.getElementById('spin').getAttribute('aria-pressed');") == "true"
    )
    browser.click("#spin")
    browser.click("#reset")
    browser.click("#png")
    browser.click("#download")
    assert (
        browser.js(
            "return performance.getEntriesByType('resource')"
            ".filter(r=>r.name.startsWith('http')).length;"
        )
        == 0
    )
    browser.command("WebDriver:SwitchToFrame", {"id": None})
    browser.screenshot(output / "structures.png")
    browser.click('[data-panel="adaptive"]')
    browser.command("WebDriver:SwitchToFrame", {"id": 1})
    browser.wait("!!document.getElementById('budget')")
    assert browser.js("return document.getElementById('budget').textContent;") == "90 / 270"
    for budget in ["91 / 270", "92 / 270", "93 / 270"]:
        browser.click("#next")
        assert browser.js("return document.getElementById('budget').textContent;") == budget
    assert browser.js("return document.getElementById('outcome').textContent;") == "反驳"
    browser.command("WebDriver:SwitchToFrame", {"id": None})
    for width in [1440, 768, 450]:
        browser.command("WebDriver:SetWindowRect", {"width": width, "height": 1000})
        for panel in ["intake", "pipeline", "structure", "adaptive", "usage"]:
            browser.click(f'[data-panel="{panel}"]')
            assert browser.js("return document.documentElement.scrollWidth <= innerWidth;")
    assert (
        browser.js(
            "return performance.getEntriesByType('resource')"
            ".filter(r=>r.name.startsWith('http')).length;"
        )
        == 0
    )
    # Firefox restricts downloads from opaque file:// iframe origins. The shipped
    # standalone link is the supported offline download path; verify real files.
    browser.navigate((site / "demo/structures.html").as_uri())
    browser.wait(
        "!!document.getElementById('message') && document.getElementById('message').hidden"
    )
    browser.click("#png")
    browser.click("#download")
    png, structure = output / "alpd-structure.png", output / "generated_structure.cif"
    for _ in range(100):
        if png.is_file() and structure.is_file():
            break
        time.sleep(0.1)
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert png.stat().st_size > 20000
    assert (
        structure.read_bytes()
        == (site / "case/run/candidates/candidate0000/generated.cif").read_bytes()
    )
    browser.command("WebDriver:DeleteSession")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--firefox", default="firefox")
    parser.add_argument("--xvfb", default="Xvfb")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    children, connection = [], None
    with tempfile.TemporaryDirectory(prefix="alpd-browser-") as temporary:
        profile = Path(temporary)
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        prefs = {
            "marionette.port": port,
            "webgl.force-enabled": True,
            "webgl.disabled": False,
            "gfx.webrender.software": True,
            "browser.shell.checkDefaultBrowser": False,
            "browser.startup.homepage": "about:blank",
            "browser.startup.homepage_override.mstone": "ignore",
            "browser.newtabpage.enabled": False,
            "network.captive-portal-service.enabled": False,
            "network.connectivity-service.enabled": False,
            "app.update.auto": False,
            "datareporting.policy.dataSubmissionEnabled": False,
            "browser.download.folderList": 2,
            "browser.download.dir": str(output),
            "browser.download.useDownloadDir": True,
            "browser.helperApps.neverAsk.saveToDisk": (
                "image/png,application/octet-stream,text/plain"
            ),
            "browser.download.always_ask_before_handling_new_types": False,
        }
        (profile / "user.js").write_text(
            "".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k, v in prefs.items())
        )
        env = {
            **os.environ,
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "MOZ_CRASHREPORTER_DISABLE": "1",
            "XDG_CACHE_HOME": str(profile / "cache"),
            "XDG_CONFIG_HOME": str(profile / "config"),
            "GSETTINGS_BACKEND": "memory",
        }
        with (output / "firefox.log").open("w") as log:
            try:
                if not env.get("DISPLAY"):
                    xvfb = shutil.which(args.xvfb)
                    if not xvfb:
                        raise RuntimeError(
                            "Xvfb or an existing DISPLAY is required for the WebGL check"
                        )
                    read_fd, write_fd = os.pipe()
                    try:
                        children.append(
                            subprocess.Popen(
                                [
                                    xvfb,
                                    "-displayfd",
                                    str(write_fd),
                                    "-screen",
                                    "0",
                                    "1600x1100x24",
                                    "-nolisten",
                                    "tcp",
                                ],
                                stdout=log,
                                stderr=log,
                                pass_fds=(write_fd,),
                                start_new_session=True,
                            )
                        )
                    finally:
                        os.close(write_fd)
                    with os.fdopen(read_fd) as display:
                        env["DISPLAY"] = ":" + display.readline().strip()
                children.append(
                    subprocess.Popen(
                        [args.firefox, "--no-remote", "--profile", str(profile), "--marionette"],
                        stdout=log,
                        stderr=log,
                        env=env,
                        start_new_session=True,
                    )
                )
                for _ in range(150):
                    if children[-1].poll() is not None:
                        raise RuntimeError("Firefox exited; inspect firefox.log")
                    try:
                        connection = socket.create_connection(("127.0.0.1", port), timeout=0.5)
                        break
                    except OSError:
                        time.sleep(0.1)
                if connection is None:
                    raise RuntimeError("Firefox did not start")
                connection.settimeout(30)
                exercise(Browser(connection), args.site.resolve(), output)
                result = {
                    "status": "passed",
                    "browser": subprocess.check_output(
                        [args.firefox, "--version"], text=True
                    ).strip(),
                    "checks": [
                        "gallery",
                        "intake",
                        "decision_evidence",
                        "tool_results",
                        "stop_reason",
                        "webgl",
                        "candidate_and_structure_switch",
                        "chain_controls",
                        "rotation",
                        "download_buttons",
                        "saved_png_and_exact_structure_bytes",
                        "adaptive_rounds",
                        "responsive_layout",
                        "no_http_resources",
                    ],
                }
                (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
                print(json.dumps(result))
            finally:
                if connection:
                    connection.close()
                for child in reversed(children):
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL)
                            child.wait(timeout=5)


if __name__ == "__main__":
    main()
