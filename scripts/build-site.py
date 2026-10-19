"""Copy shared brand assets into the self-contained GitHub Pages directory."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parent.parent
assets = root / 'docs' / 'assets'
assets.mkdir(parents=True, exist_ok=True)
for source, name in [('web/js/themes.js', 'themes.js'), ('web/favicon.svg', 'favicon.svg')]:
    shutil.copyfile(root / source, assets / name)
palette = (root / 'web/css/app.css').read_text().split('* { box-sizing:', 1)[0]
(assets / 'palette.css').write_text(palette.rstrip() + '\n')
print('Website assets prepared')
