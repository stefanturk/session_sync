# Ableton notes

Things learned from real Live 11 projects, and what the adapter does about them.

## Project folder

A project folder holds the `.als` Live Set, an `Ableton Project Info/` folder
(sometimes empty), `Samples/Recorded/` for takes, and `Backup/` full of Live's
own timestamped auto-saves.

Tracked: `.als`, `Ableton Project Info/**`, `Samples/**`, and any other media in
the folder.

Not tracked, because Live or macOS regenerates them:

| Excluded | Why |
|---|---|
| `Backup/` | Live's own auto-saves; repository history replaces them. A real project had 115 backup files. |
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

## Collect All and Save

Not automatable — the adapter returns a checklist instead of driving Live's UI.
The value we add is telling the user *specifically* which files would not
travel, before they push, rather than a generic reminder.

## Plugins

VST/AU plugins are never copied into a repository. A plugin/environment report
is a later phase; remember AU is Mac-only, so a Windows collaborator may need
rendered audio.
