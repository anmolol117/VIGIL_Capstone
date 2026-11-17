#!/bin/zsh
# Renders poster.html to an A0 landscape PDF (vector text) and a PNG preview.
cd "${0:A:h}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
URL="file://$PWD/poster.html"
"$CHROME" --headless --disable-gpu --no-pdf-header-footer --print-to-pdf="$PWD/VIGIL_Poster_A0.pdf" "$URL" 2>/dev/null
"$CHROME" --headless --disable-gpu --hide-scrollbars --window-size=4494,3179 --screenshot="$PWD/VIGIL_Poster.png" "$URL" 2>/dev/null
ls -la VIGIL_Poster_A0.pdf VIGIL_Poster.png
