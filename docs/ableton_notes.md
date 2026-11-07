# Ableton notes

Things learned from real Live 11 projects, and what the adapter does about them.

## Project folder

A project folder holds the `.als` Live Set, an `Ableton Project Info/` folder
(sometimes empty), `Samples/Recorded/` for takes, and `Backup/` full of Live's
own timestamped auto-saves.

Pushed (see Consolidate below): every `.als`, the audio and Max devices the Sets
use, and small non-audio files (`Ableton Project Info/**`, notes). Audio and video
no Set uses stay on the computer that has them.

Not tracked, because Live or macOS regenerates them:

| Excluded | Why |
|---|---|
| `Backup/` | Live's own auto-saves; repository history replaces them. A real project had 115 backup files. |
| `Past Revisions/` | Written by Push itself: one main Set per revision. |
| `*.asd` | Analysis files, regenerated on demand. |
| `.DS_Store`, `._*`, `Icon\r` | macOS metadata. |
| `*.tmp`, `*.lock`, `.sessionsync/` | Working files. |

## The .als file

Gzipped XML. The root element carries the Live version:

```xml
<Ableton MinorVersion="11.0_11300" …>
```

Read the version from the first few KB rather than decompressing the whole
thing — a modest set is ~1.7 MB of XML from a 97 KB file. Reading to EOF also
raises `BadGzipFile` if anything is appended after the gzip stream, so the
version probe reads a fixed-size chunk instead.

## Sample references

Every referenced file appears as a `FileRef`:

```xml
<FileRef>
  <RelativePathType Value="3" />
  <RelativePath Value="Samples/Recorded/Trumpet 0001 [2024-06-17 202348].aif" />
  <Path Value="/Users/…/Take My Heart Project/Samples/Recorded/Trumpet 0001 ….aif" />
  <Type Value="2" />
  <LivePackName Value="" />
</FileRef>
```

`referenced_media()` resolves each one:

- `RelativePath` resolves inside the project → **inside**, travels fine.
- otherwise `Path` exists on this Mac → **outside**, works here and nowhere
  else. This is precisely what Collect All and Save would pull in.
- neither resolves → **missing**, the project is already incomplete here.

Note the absolute `Path` is often stale (it records where the sample was
imported from, which may since have moved), so a failed absolute path with a
working relative path is normal and not a problem.

### Filtering out Live's own content

`FileRef` is also used for built-in devices and Pack content, which exist on
every machine with the same Live installed and must never be flagged or
collected. A first run on a real project reported twelve "missing samples" that
were all Core Library devices. Skipped when:

- `LivePackName` is set, or
- the path is inside an `Ableton Live *.app` bundle, or contains
  `/Core Library/` or `/Factory Packs/`, or
- the extension is not a known audio/video type (devices have no extension).

## Consolidate (our Collect All and Save)

`adapters/ableton/consolidate.py` runs on every Push and status check.

- **What a Set needs**: each `FileRef` inside a `<SampleRef>` (audio, video), plus
  `.amxd` Max devices. FileRefs elsewhere only record where a preset was loaded
  from and are not dependencies.
- **Resolving** follows `RelativePathType`: `3` is relative to the project folder,
  `1` to the Set's own folder, otherwise the absolute `Path`. `5` is Live's Core
  Library and is skipped, as is any Pack whose `LivePackId` starts with
  `www.ableton.com`. Samples from other Packs (third-party, user-made) are copied.
- **Outside files** go to `Samples/Imported/<name>`, with ` (2)` added if another
  file ever had that name. The same file keeps its name across revisions; the
  names come from every manifest ever published, so nothing new can take an old
  revision's path.
- **Rewriting** is done only for Live 11+ and only on the copy that goes to the
  shared folder: `RelativePathType`, `RelativePath` and `Path` inside the affected
  `FileRef` blocks change, every other byte stays identical, and the result is
  gzipped with a fixed timestamp so the same Set always gives the same hash.
  Set-relative references inside the project become project-relative too, so the
  copy in `Past Revisions/` still finds its samples.
- **Verifying**: the rewritten Set is gunzipped, parsed as XML, and every
  reference must now point at a file this Push sends. Any failure stops Push
  before anything uploads.
- The manifest stores the local Set's hash as `source_sha256`, so the person who
  pushed is not shown a change, or sent their own Set back on Pull.

Measured on real projects: "Sketches 2026" (16 GB folder) pushes 12.3 GB and leaves
4.7 GB of unused takes behind, collecting 14 samples from `~/Music/Beat Thief` and
another project; "Overtime" pushes 4.3 of 4.6 GB.

### Live 10

Live 10 stores a reference as `RelativePathElement`s plus a binary macOS alias in
`<Data>`, with no plain `Path`. That cannot be rewritten safely, so a Live 10 main
Set stops Push ("re-save in Live 11 or newer"); other Live 10 Sets in the folder
are left out and listed.

## Plugins

VST/AU plugins are never copied into a repository. A plugin/environment report
is a later phase; remember AU is Mac-only, so a Windows collaborator may need
rendered audio.
