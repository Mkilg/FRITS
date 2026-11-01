from frits import sounds


def test_sound_is_off_by_default():
    assert sounds.SOUND_ON_BY_DEFAULT is False


def test_every_listed_sound_file_exists_and_is_small():
    for sound in [*sounds.CUES.values(), *sounds.TRACKS.values()]:
        f = sounds.sounds_dir() / sound.path
        assert f.is_file(), sound.path
        data = f.read_bytes()
        assert data[:3] == b"ID3" or data[0] == 0xFF, f"{sound.path} is not an MP3"
        assert len(data) < 1_000_000, f"{sound.path} is too big for the USB stick"


def test_required_cues_exist():
    assert {"done", "sent", "shutdown"} <= set(sounds.CUES)
    assert sounds.cue_file("done").is_file()
    assert sounds.track_file("morgen").is_file()


def test_license_and_player_ship_with_the_sounds():
    assert (sounds.sounds_dir() / "LICENSE.md").is_file()
    js = (sounds.sounds_dir() / "frits-sound.js").read_text()
    assert 'getAttribute("data-frits-sound") === "on"' in js  # off unless switched on


def test_every_sound_file_is_listed_in_the_license():
    license_text = (sounds.sounds_dir() / "LICENSE.md").read_text()
    for sound in [*sounds.CUES.values(), *sounds.TRACKS.values()]:
        assert sound.path in license_text


def test_every_mood_plays_an_existing_track():
    assert sounds.DEFAULT_MOOD in sounds.MOODS
    assert {"entspannt", "motivierend", "abenteuerlich"} <= set(sounds.MOODS)
    for mood in sounds.MOODS.values():
        assert mood.track in sounds.TRACKS


def test_steuer_quest_always_gets_adventure_music():
    assert sounds.track_for("entspannt", "/steuer/2025") == "abenteuer"
    assert sounds.track_for("motivierend", "/") == "schwung"
    assert sounds.track_for("gibt-es-nicht", "/") == sounds.MOODS[sounds.DEFAULT_MOOD].track
