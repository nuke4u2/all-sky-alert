"""Run manually on the Pi, with All Sky Alert stopped. No desktop preview or mail."""
import argparse
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('test-artifacts/pi-camera/diagnostic.jpg'))
    args = parser.parse_args()
    from picamera2 import Picamera2
    from libcamera import controls
    print('Detected cameras:', Picamera2.global_camera_info())
    camera = Picamera2()
    try:
        print('Model:', camera.camera_properties.get('Model', 'Unknown'))
        print('Sensor resolution:', camera.sensor_resolution)
        camera.configure(camera.create_still_configuration(main={'size': camera.sensor_resolution}, buffer_count=1))
        print('Focus controls:', {key: camera.camera_controls.get(key) for key in ('AfMode', 'AfTrigger', 'LensPosition')})
        camera.start(show_preview=False)
        time.sleep(2)
        if all(key in camera.camera_controls for key in ('AfMode', 'AfTrigger')):
            camera.set_controls({'AfMode': controls.AfModeEnum.Auto})
            job = camera.autofocus_cycle(wait=False)
            print('Autofocus focused:', camera.wait(job, timeout=20))
        else:
            print('Autofocus unsupported by this camera/stack')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        camera.wait(camera.capture_file(str(args.output), wait=False), timeout=20)
        print('Image:', args.output.resolve(), 'bytes:', args.output.stat().st_size)
    except TimeoutError:
        camera.cancel_all_and_flush()
        raise
    finally:
        try:
            camera.stop()
        finally:
            camera.close()
    print('Camera stopped and released')


if __name__ == '__main__':
    main()
