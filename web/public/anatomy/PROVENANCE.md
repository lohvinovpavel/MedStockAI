# Anatomical figures

`male_template_with_organs.svg`, `female_template_with_organs.svg`

| | |
|---|---|
| Author | Mikael Häggström |
| Source | https://commons.wikimedia.org/wiki/Human_body_diagrams |
| Licence | **CC0 1.0 Universal** — public domain dedication |

CC0 waives copyright entirely: commercial use, modification and redistribution
are all permitted with no attribution requirement. The credit shown in the UI is
courtesy, not obligation.

Servier Medical Art was the other candidate and is also excellent, but it is
CC BY 4.0 — every screen showing a figure would carry an attribution duty to
honour and maintain. For a hospital-facing product the absence of a compliance
surface was worth more than the difference in artwork.

## Local modifications

Both files are edited copies, which CC0 permits:

1. **viewBox added.** Neither file had one. The male declared `1363x1211` while
   its body raster is 2389 tall, so the figure was cropped at the hips; it now
   carries `viewBox="0 0 1363 2440"`. Both use `width/height="100%"` so they
   scale together in a flex row.
2. **Placeholder callouts removed.** The templates ship with "Header" and
   "Example text" labels plus leader lines, meant to be replaced by whoever uses
   them. 19 text nodes and 20 leader paths stripped from each. The organs are
   filled rasters, so removing the thin `fill:none; stroke:#000000` paths cannot
   touch anatomy.

The callouts were used before deletion: `web/lib/anatomy.ts` derives its organ
coordinates by pairing each label with its nearest leader line and taking the far
end. Re-deriving after swapping artwork means re-running that pairing against the
unstripped original, not measuring by eye.

## Faces

Both figures have photorealistic faces. That is unresolved: a realistic face on a
view headed with a named patient can read as a picture *of* that patient, which
it is not. An earlier attempt masked them with a blurred ellipse and was reverted
— the masks were positioned from screenshot estimates and missed. Anything
overlaying these files must convert through the rendered `getBoundingClientRect`
and the viewBox scale (male 3.94 units/px, female 3.74 at 620px tall) rather than
estimating from a screenshot.
