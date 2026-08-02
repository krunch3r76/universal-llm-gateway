# HTML viewer snippet

Copy from exemplar: `tmp/email-capture-postprocess/email-capture-postprocess.html`

Minimal structure per diagram:

```html
<figure class="diagram-wrap">
  <div class="diagram-toolbar">
    <button type="button" data-action="zoom-in">+</button>
    <button type="button" data-action="zoom-out">−</button>
    <button type="button" data-action="reset">Reset</button>
    <button type="button" data-action="open">Open SVG</button>
    <span class="zoom-label" data-zoom-label>100%</span>
  </div>
  <div class="diagram-viewport">
    <div class="diagram-stage" data-src="images/.../diagram.svg">
      <object type="image/svg+xml" data="images/.../diagram.svg"
              width="W" height="H" aria-label="..."></object>
    </div>
  </div>
  <figcaption>...</figcaption>
</figure>
```

JS: query `.diagram-wrap`, apply `transform: scale()` on `.diagram-stage`, pan via viewport scroll on mousedown/mousemove. See exemplar script block.

Set `width`/`height` on `<object>` from rendered SVG dimensions (or viewBox).
