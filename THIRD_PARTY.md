# Bundled libraries

The application includes these browser libraries in `web/vendor`:

| Library | License | Source |
| --- | --- | --- |
| Preact and Preact hooks | MIT; `LICENSE-preact` | https://github.com/preactjs/preact |
| htm | Apache-2.0; `LICENSE-htm` | https://github.com/developit/htm |
| qrcode-generator | MIT; `LICENSE-qrcode` | https://github.com/kazuhikoarase/qrcode-generator |
| Signalsmith Stretch | MIT; `LICENSE-signalsmith-stretch` | https://github.com/Signalsmith-Audio/signalsmith-stretch |

The vendored QR module has an ES-module export. The Signalsmith module includes
the browser wrapper and its compiled WebAssembly payload. Preserve the license
files when redistributing these libraries.

Python dependencies and downloaded model weights have their own licenses and
terms. They are installed or downloaded separately and are not bundled in this
repository. Font files are served by Google Fonts; the app also works with its
system-font fallbacks.
