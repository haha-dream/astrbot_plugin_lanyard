# Changelog

All notable changes to this project will be documented in this file.

## v1.0.6 - 2026-03-21

### Changed

- Replaced the Lanyard WebSocket implementation with HTTP polling.
- Added configurable `poll_interval` support for periodic presence fetching.
- Switched message delivery from whole-presence replay to per-activity incremental push.
- Pushed activities are now tracked individually and only resent when that activity changes.
- Activity fingerprints are now based on the final rendered activity text to avoid duplicate pushes caused by hidden field changes.

### Documentation

- Updated README to describe the HTTP polling workflow and incremental push behavior.
