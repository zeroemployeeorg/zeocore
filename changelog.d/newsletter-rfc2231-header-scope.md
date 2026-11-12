bump: patch
Newsletter decode: the refusal of an RFC 2231 boundary continuation now looks only inside real header sections of the message (a "--" signature line or a body line that looks like a Content-Type header does not count), and the header, blank-line and boundary-line counts before parsing read every line ending (CR LF, LF, bare CR) as one line end. Follow-up to #217.
