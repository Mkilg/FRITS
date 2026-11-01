"""FRITS in the browser: the same app, run by Pyodide (Python compiled to WebAssembly).

Nothing here runs on a normal computer install. The browser page (see `browser/` in the
repository) loads Pyodide, installs FRITS and calls `runtime.start()`. Every click in the app
then becomes a call to `asgi.handle()` instead of a request to a server: the data never leaves
the browser, except the already-redacted text that goes to the AI the user chose.
"""
