bump: patch
Newsletter decode: a multipart boundary written as an RFC 2231 continuation (`boundary*0=`, `boundary*=`) is refused before parsing, because the boundary-line count could not see it; the docs note that an indented `3.` is refused by the ordered-list check. Follow-up to #126.
