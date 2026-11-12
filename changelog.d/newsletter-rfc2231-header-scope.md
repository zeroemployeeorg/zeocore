bump: patch
Newsletter decode: the refusal of an RFC 2231 boundary continuation now looks only inside Content-Type header fields (folded lines and nested part headers included), so body prose such as "Example; boundary*0=ab" is no longer refused. Follow-up to #217.
