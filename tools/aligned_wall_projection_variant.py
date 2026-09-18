"""Build a hash-pinned wall-depth projection control for the aligned capture.

Replay only: material/color correspondence and campaign scope are unvalidated.
"""
import equipment_projection_variants as experiment


if __name__ == '__main__':
    experiment.LAYOUTS = {
        '621f3814c3486f8a58c3bc252e7a9ab59b70b58984f66be30367d809f330c806':
        dict(name='wall-depth', position=2, world=1, scratch=4, output=0)
    }
    experiment.main()
