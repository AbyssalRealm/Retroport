# Third-party notices

Parts of retroport are adapted from **Conjure** by TheDaveCalaz (MIT License).
Adapted code is marked in each module docstring:

- `retroport/dbc.py` - WDBC engine, CreatureModelData/DisplayInfo row layouts, TextureVariations
- `retroport/sql.py` - AzerothCore repoint SQL
- `retroport/formats/md20.py` - texture-path baking, animation-table classifier
- `retroport/convert/texbake.py` - in-game path rules
- `retroport/check.py` - multi-LOD vertex-span gate, external .anim checks
- `retroport/safeio.py` - verify-before-success / never-overwrite-input rules

Conjure license:

```
MIT License

Copyright (c) 2026 TheDaveCalaz

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

```
