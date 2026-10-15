# Private move photos

Office: Schedule → move → Photos → choose up to ten photos → Upload.
Assigned crew: Schedule → move → Photos. Click a thumbnail to open the full photo.
Clients: private moving file → Files. Photos uploaded by the client are shared with the crews assigned to that moving file.

Crew access is checked on each list/download/upload request. Removing an assignment removes photo access without requiring a new sign-in. The crew account must be linked to its employee record. Job-specific photos stay scoped to that job; moving-file photos are available to assigned crews working on that file. Files are served through authenticated endpoints with no-store headers, never a public uploads directory.

## Supported mode

Set `CROWNOPS_UPLOAD_MODE=photos` and `CROWNOPS_UPLOADS_ENABLED=1` after deploying this release. The existing general external-actions, email and payment flags are independent and remain unchanged.

This mode accepts JPEG, PNG and WebP photos up to 15 MiB / 24 megapixels. Animated images, HEIC, SVG, PDF and videos are rejected; export other photo formats as JPEG. The output is a newly encoded JPEG of at most 2048 pixels on its longest side. Original files are discarded after conversion. Embedded EXIF/GPS, profiles, comments and appended data are not copied; EXIF orientation is applied before metadata removal.

Decoding runs in a separate process with a minimal environment, memory/CPU/output limits, an allowlist of decoders and a wall timeout. This is a bounded image-conversion process, not a complete OS sandbox or antivirus scanner. `Photo checked` identifies this path; `Clean` remains the distinct configured-scanner result. Neither is a guarantee against every possible threat. Keep Pillow updated through the repository's dependency-review process. PDF/video and fleet-document uploads remain disabled in photo-only mode; the general file mode still requires a real scanning command in production.

Production startup runs a real PNG-to-JPEG self-test and fails if the processor is unavailable. `/api/health` reports only the public `private_photos_available` boolean; staff and client upload-policy responses supply supported formats and UI availability.

## Storage and recovery

Photos use the existing private uploads directory on the attached Railway volume. At the current 500 MB allocation, the default photo quota is 128 MiB, with 256 MiB of free space reserved for the database and recovery work. Original-size temporary files are checked against the same reserve. Nothing is automatically deleted when a quota is reached. Increase the volume / photo storage capacity before a large rollout; don't merely raise the quota on a full volume.

New file metadata, content hashes and attachment records are written together. Failed uploads remove temporary/output files; repeated identical photos for the same moving file/job reuse the saved photo. Existing portable backup/restore code includes these private JPEG files and checks their stored hashes. Railway volume backups and offsite retention still require the owner's separate hosting setup.

Implementation references: [OWASP file uploads](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html), [Pillow image limits](https://pillow.readthedocs.io/en/stable/reference/Image.html), [EXIF orientation](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html).
