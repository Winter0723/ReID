# Camera gateway

Standalone discovery, configuration and RTSP publishing, with no MyReID backend or database imports. Python standard library only; local capture and probing require FFmpeg. MediaMTX is the independently deployed RTSP server.

Run from the repository root:

```powershell
.\scripts\run_camera_gateway.ps1
python -m camera_gateway scan
python -m camera_gateway status
```

Configuration: `tmp/camera-bridge/sources.json` (or set `CAMERA_GATEWAY_DIR`). Physical devices are discovered and enabled automatically; virtual devices are opt-in by stable ID. IP cameras and NVR channels use `kind: rtsp` with a configured upstream URL. Changes reconcile individual paths through the MediaMTX control API and persist for restart.

Standard RTSP clients can read `rtsp://reader:<password>@127.0.0.1:18554/local/<id>` using the generated reader password in `credentials.json`. No backend API is required.

See [deployment, configuration and limitations](../docs/camera-gateway.md) and [example configuration](sources.example.json). To reuse independently, retain this package, `docker-compose.camera-gateway.yml`, and the Windows launch scripts (`run_camera_gateway.ps1`, `run_camera_bridge.ps1`, `camera_bridge.py`) in the same directory layout. Linux can use the module CLI directly; its V4L2 adapter requires hardware validation.
