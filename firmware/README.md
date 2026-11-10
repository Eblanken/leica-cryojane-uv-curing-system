# Firmware

PlatformIO projects for the replacement design go here, one folder per target. There is no project yet because the MCU has not been chosen.

Create one with:

```
pixi run -e firmware pio project init --project-dir firmware/<target> --board <board>
```

The relay mux bridge firmware is bench tooling, so it lives with its driver in `src/instruments/xl9535_relay_mux/firmware/`.
