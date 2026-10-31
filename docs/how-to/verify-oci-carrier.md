# Verify an OCI carrier without loading it

`zeo_core.release.oci.verify_oci_carrier` checks a bounded uncompressed OCI layout
tar against independently supplied expectations. It never extracts the archive,
decompresses a layer, loads an image, invokes Docker or authorizes deployment.

```python
from zeo_core.release.oci import OciCarrier, verify_oci_carrier

# Supply these fields from an authenticated, independently admitted release.
expectations = OciCarrier.model_validate(trusted_carrier_fields)
with open(archive_path, "rb") as stream:
    verified = verify_oci_carrier(stream, expectations)
```

The reader stops at the declared byte limit plus one, checks the archive digest,
and uses a bounded memory spool that spills to a temporary file. It accepts only
regular entries named `oci-layout`, `index.json` or `blobs/sha256/<64 lowercase hex>`.
It refuses duplicate names, links, directories, devices, extension headers,
nonzero padding, nonzero trailing bytes, missing metadata, invalid JSON, excessive
entry counts, wrong blob hashes, descriptor sizes and platform mismatches. Metadata
JSON is limited to 1 MiB. No untrusted archive pathname is used as an output path.

The current carrier profile contains one manifest/platform. A multi-platform index
is refused; a `docker save` archive is refused. `OciVerificationError.code` is a
stable refusal category and does not include archive contents.

This result does **not** verify a build-receipt signature, source revision, base
image provenance, decompressed rootfs, current promotion policy or installed
predecessor. These must be verified by the admission and actuator layers before
loading, together with the expanded-byte bound. Activation, migration, health checks
and rollback remain separate effects. An uncertain activation must not be retried
because offline verification passed.
