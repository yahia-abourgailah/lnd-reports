# Vendored fonts

`DejaVuSans.ttf` and `DejaVuSans-Bold.ttf`, copied from Debian's
`fonts-dejavu-core`. Licence in `LICENSE.txt` (Bitstream Vera, permissive:
use, copy, modify and redistribute, with the notice kept).

## Why they are in the repository

fpdf2's built-in fonts are Latin-1 only. The prose this platform puts into a
PDF is not: every metric definition carries an em dash, and the reconciliation
note is nothing but em dashes. Encoding the file with a core font would either
raise on the first `—` or silently transliterate the sentence somebody has to
read out to L&D.

The alternative to vendoring was to install a font package in the image and
look it up by path at runtime. That gives two possible outputs — one where the
font is found and one where it is not — and the fallback would be exercised
exactly where nobody is watching. A report generated on this machine, in CI and
in the container is the same bytes because the font travels with the code.
