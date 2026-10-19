"""Runs worker jobs one at a time (they share one GPU) and reports progress."""
import asyncio
import contextlib
import json
import re
import time

from . import config, store, catalog

_PCT = re.compile(rb"(\d{1,3})%\|")


class Jobs:
    def __init__(self, on_change):
        self.on_change = on_change          # async callback(song_id)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.status = {}                    # song_id -> {task, label, progress, state, ...}
        self.waiters = {}                   # job key -> Future with result
        self._task = None
        self.process = None

    def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task: self._task.cancel()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try: await asyncio.wait_for(self.process.wait(),5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self._task:
            with contextlib.suppress(asyncio.CancelledError): await self._task

    def submit(self, task, song_id, args=None):
        if song_id in self.status: raise ValueError("A job is already running for this song")
        key = f"{task}:{song_id}:{time.time()}"
        fut = asyncio.get_running_loop().create_future()
        self.waiters[key] = fut
        self.status.setdefault(song_id, {})
        self.status[song_id] = {"task": task, "label": "Waiting for the previous song", "progress": 0.0,
                                "state": "queued", "since": time.time()}
        self.queue.put_nowait((key, task, song_id, args or {}))
        return fut

    def snapshot(self):
        return self.status

    async def _loop(self):
        while True:
            key, task, song_id, args = await self.queue.get()
            fut = self.waiters.pop(key)
            try:
                result = await self._run(task, song_id, args)
                fut.set_result(result)
            except Exception as e:  # noqa: BLE001
                self.status.pop(song_id, None)
                await self.on_change(song_id)
                if not fut.done():
                    fut.set_exception(e)

    async def _run(self, task, song_id, args):
        st = {"task": task, "label": "Starting", "progress": 0.0, "state": "running", "since": time.time()}
        self.status[song_id] = st
        await self.on_change(song_id)
        env=config.cuda_env()
        if args.get("staging"):
            env.update(STEMROOM_DATA=args["staging"], STEMROOM_MODELS=str(config.MODELS))
        proc = await asyncio.create_subprocess_exec(
            config.PYTHON, "-m", "server.worker", task, song_id, json.dumps(args),
            cwd=str(config.ROOT), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self.process = proc
        rng = [0.0, 1.0]
        result = {}
        log_tail = []
        last_push = [0.0]

        async def push(force=False):
            now = time.time()
            if force or now - last_push[0] > 0.4:
                last_push[0] = now
                await self.on_change(song_id)

        async def read_stdout():
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("@@"):
                    continue
                ev = json.loads(line[2:])
                kind = ev["event"]
                if kind == "stage":
                    rng[0], rng[1] = ev["lo"], ev["hi"]
                    st.update(label=ev["label"], progress=ev["lo"])
                    await push(True)
                elif kind == "frac":
                    st["progress"] = rng[0] + (rng[1] - rng[0]) * ev["value"]
                    await push()
                elif kind == "meta":
                    await push(True)
                elif kind == "file":
                    result["file"] = ev["path"]
                elif kind == "error":
                    result["error"] = ev["message"]

        async def read_stderr():
            buf = b""
            while chunk := await proc.stderr.read(4096):
                buf += chunk
                parts = re.split(rb"[\r\n]", buf)
                buf = parts.pop()
                for p in parts:
                    m = _PCT.search(p)
                    if m:
                        f = int(m.group(1)) / 100
                        st["progress"] = max(st["progress"], rng[0] + (rng[1] - rng[0]) * f)
                        await push()
                    elif p.strip():
                        log_tail.append(p.decode(errors="replace"))
                        del log_tail[:-40]

        await asyncio.gather(read_stdout(), read_stderr())
        code = await proc.wait()
        self.process = None
        if code != 0 or "error" in result:
            msg = result.get("error") or (log_tail[-1] if log_tail else f"worker exited with {code}")
            if "403" in msg and "download" in msg.lower():
                msg = "YouTube refused the download (it does this now and then). Try again in a minute."
            (store.song_dir(song_id) / "worker.log").write_text("\n".join(log_tail))
            if args.get("staging"):
                store.update_song(song_id, replacementError=msg)
                catalog.replacement(song_id,clear=True)
            elif task == "process":
                store.update_song(song_id, status="error", error=msg)
            st.update(state="error", label=msg)
            self.status.pop(song_id, None)
            await push(True)
            raise RuntimeError(msg)
        if args.get('staging'):
            from .library import promote
            promote(song_id,args)
        else:
            song=store.read_song(song_id)
            if song: catalog.upsert(song)
        self.status.pop(song_id, None)
        await push(True)
        from .library import changed
        await changed()
        return result
