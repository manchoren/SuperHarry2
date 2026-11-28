# Input downloads and conversation attachments

**Status:** Approved on 2026-11-28. Validation seams confirmed by the owner.

## Problem

The Inputs page lists both standalone inputs and inputs attached to a conversation. The input download view currently requires every input to have a message in a conversation, so downloading a standalone input returns 404 even when it belongs to the signed-in user. In chat, attachments are listed inside their originating messages, and the preview supports PDF, PNG, and JPEG only.

## User outcome

Users can download their own inputs, find a conversation's attachments together below its messages, preview common visual media, and download office documents with clear spacing between file descriptions and actions.

## Requirements

1. An authenticated user can download any input they own, whether it is standalone or attached to a conversation.
2. A download remains unavailable when the input belongs to another user. If an input has a message, its conversation must also belong to that same user. Do not expose file contents through an unauthenticated or cross-owner URL.
3. Show currently available attachments from the open conversation in one section after the conversation messages, in attachment order. Each file appears once with its name and relevant actions. Keep message text and removed-attachment notices in the transcript.
4. Show inline previews for PNG and JPEG images and playable video for MP4 and WebM files. Retain the existing PDF preview. The preview section also provides the file's download action.
5. Show PDF, DOCX, XLSX, PPTX, ODT, ODS, and ODP attachments as named downloadable files. Do not attempt to render office document contents in the browser.
6. Other attachment formats remain downloadable and are presented without a preview.
7. Separate file names or supporting text from their action buttons with visible spacing. Keep actions keyboard-accessible and usable on narrow screens and in both themes.
8. Keep the existing attachment size/count limits, persistence, conversation ownership, and file deletion behavior.

## Acceptance criteria

- A standalone input owned by the current user downloads with its original filename and exact payload.
- An input attached to a conversation owned by the current user still downloads. Another user's input, and an input attached to a conversation owned by someone else, return 404.
- The conversation's active attachments appear together after the messages, once each, with names and correct actions. Removed attachment notices remain visible in their messages.
- PNG/JPEG previews render; MP4/WebM attachments expose native playback controls; PDF retains its current preview; office files and other formats can be downloaded.
- The file list separates text from its controls and remains readable and operable with keyboard focus, at narrow widths, and in light and dark themes.

## Proposed validation seam

- **Django view tests:** extend `tests/test_chat.py` or add focused input-view tests. Use an isolated database and synthetic file bytes to check standalone and conversation-linked downloads, filenames and payloads, plus 404 responses for foreign owners and mismatched conversation ownership.
- **Browser smoke:** extend `tests/ui_file_preview_smoke.py` with synthetic conversation attachments. Check the consolidated section's position and ordering, image/PDF previews, native video controls, office and unknown-format downloads, and the visible gap around actions at desktop and narrow widths. Keep its temporary database and synthetic gateway fixture; do not contact a model provider or external service.
- **Visual/accessibility review:** inspect the affected chat and Inputs surfaces in light and dark themes; verify keyboard access, visible focus, and no horizontal page overflow.

The current implementation has no explicit video or office-document preview contract. This proposal treats office documents as downloadable files and video as browser-native playback; it does not add server-side conversion or document rendering.
