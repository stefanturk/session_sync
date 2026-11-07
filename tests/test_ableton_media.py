"""Finding the audio a Live Set uses but does not carry with it.

Live Sets reference samples by a project-relative path and an absolute path.
A Set that has not had Collect All and Save run on it typically resolves only
through the absolute path — fine on the Mac it was made on, missing audio for
everyone else.
"""

import gzip

import pytest

from sessionsync.adapters.ableton.adapter import AbletonAdapter

SET_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton MinorVersion="11.0_11300">
  <LiveSet>{refs}</LiveSet>
</Ableton>
"""

REF_TEMPLATE = """
  <SampleRef>
    <FileRef>
      <RelativePathType Value="3" />
      <RelativePath Value="{relative}" />
      <Path Value="{absolute}" />
      <Type Value="2" />
    </FileRef>
  </SampleRef>
"""


@pytest.fixture
def adapter():
    return AbletonAdapter()


def write_set(project, refs):
    xml = SET_TEMPLATE.format(
        refs="".join(REF_TEMPLATE.format(**ref) for ref in refs)
    )
    (project / "My Song.als").write_bytes(gzip.compress(xml.encode("utf-8")))


@pytest.fixture
def collected(tmp_path):
    """A project whose audio lives inside it, the way Collect All and Save leaves it."""
    project = tmp_path / "My Song"
    (project / "Samples" / "Imported").mkdir(parents=True)
    (project / "Samples" / "Imported" / "Loop.wav").write_bytes(b"loop")
    write_set(
        project,
        [{"relative": "Samples/Imported/Loop.wav", "absolute": "/elsewhere/Loop.wav"}],
    )
    return project


@pytest.fixture
def uncollected(tmp_path):
    """A project that points at audio sitting somewhere else on this computer."""
    project = tmp_path / "My Song"
    project.mkdir(parents=True)
    library = tmp_path / "Sample Library"
    library.mkdir()
    (library / "Big Kick.wav").write_bytes(b"kick")
    write_set(
        project,
        [{"relative": "Samples/Imported/Big Kick.wav", "absolute": str(library / "Big Kick.wav")}],
    )
    return project


class TestReferencedMedia:
    def test_audio_inside_the_project_travels_with_it(self, adapter, collected):
        report = adapter.referenced_media(collected)

        assert report.inside == ["Samples/Imported/Loop.wav"]
        assert report.outside == []
        assert report.missing == []

    def test_audio_that_only_exists_elsewhere_on_this_mac_is_flagged(
        self, adapter, uncollected, tmp_path
    ):
        report = adapter.referenced_media(uncollected)

        assert report.inside == []
        assert report.outside == [str(tmp_path / "Sample Library" / "Big Kick.wav")]

    def test_audio_that_is_nowhere_at_all_is_reported_as_missing(self, adapter, tmp_path):
        project = tmp_path / "My Song"
        project.mkdir()
        write_set(
            project,
            [{"relative": "Samples/Imported/Gone.wav", "absolute": "/nowhere/Gone.wav"}],
        )

        report = adapter.referenced_media(project)

        assert report.missing == ["Samples/Imported/Gone.wav"]

    def test_a_set_it_cannot_read_reports_nothing_rather_than_crashing(
        self, adapter, tmp_path
    ):
        project = tmp_path / "My Song"
        project.mkdir()
        (project / "My Song.als").write_bytes(b"not a live set")

        report = adapter.referenced_media(project)

        assert report.inside == report.outside == report.missing == []

    def test_the_same_sample_used_twice_is_only_reported_once(self, adapter, collected):
        write_set(
            collected,
            [
                {"relative": "Samples/Imported/Loop.wav", "absolute": "/elsewhere/Loop.wav"},
                {"relative": "Samples/Imported/Loop.wav", "absolute": "/elsewhere/Loop.wav"},
            ],
        )

        assert adapter.referenced_media(collected).inside == ["Samples/Imported/Loop.wav"]


class TestThingsThatAreNotProjectAudio:
    def test_live_s_own_built_in_devices_are_not_treated_as_missing_audio(
        self, adapter, tmp_path
    ):
        project = tmp_path / "My Song"
        project.mkdir()
        write_set(
            project,
            [
                {
                    "relative": "",
                    "absolute": "/Applications/Ableton Live 11 Suite.app/Contents/"
                    "App-Resources/Core Library/Devices/Audio Effects/Compressor",
                }
            ],
        )

        report = adapter.referenced_media(project)

        assert report.outside == []
        assert report.missing == []

    def test_a_sample_from_an_installed_live_pack_is_not_flagged(self, adapter, tmp_path):
        project = tmp_path / "My Song"
        project.mkdir()
        xml = SET_TEMPLATE.format(
            refs="""
  <SampleRef><FileRef>
    <RelativePathType Value="1" />
    <RelativePath Value="Samples/Kick.wav" />
    <Path Value="/Users/x/Music/Ableton/Factory Packs/Drums/Kick.wav" />
    <LivePackName Value="Core Library" />
  </FileRef></SampleRef>
"""
        )
        (project / "My Song.als").write_bytes(gzip.compress(xml.encode()))

        report = adapter.referenced_media(project)

        assert report.outside == []
        assert report.missing == []

    def test_a_real_audio_file_outside_the_project_is_still_flagged(
        self, adapter, uncollected
    ):
        assert len(adapter.referenced_media(uncollected).outside) == 1


class TestValidationUsesIt:
    def test_a_collected_project_gets_no_media_warning(self, adapter, collected):
        warnings = adapter.validate_project(collected).warnings

        assert not any("Push copies them" in w for w in warnings)

    def test_an_uncollected_project_is_told_push_copies_the_samples_in(
        self, adapter, uncollected
    ):
        warnings = adapter.validate_project(uncollected).warnings

        assert any("Push copies them" in w for w in warnings)
        assert any("1 sample" in w for w in warnings)

    def test_the_warning_names_a_few_of_the_files_so_it_is_actionable(
        self, adapter, uncollected
    ):
        warnings = " ".join(adapter.validate_project(uncollected).warnings)

        assert "Big Kick.wav" in warnings

    def test_missing_audio_is_an_error_not_just_a_warning(self, adapter, tmp_path):
        project = tmp_path / "My Song"
        project.mkdir()
        write_set(
            project, [{"relative": "Samples/Gone.wav", "absolute": "/nowhere/Gone.wav"}]
        )

        result = adapter.validate_project(project)

        assert any("cannot be found" in w for w in result.warnings)
