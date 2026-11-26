# Changelog

## [1.1.0-beta.2](https://github.com/TheLuBu/ha-gira-one/compare/v1.1.0-beta.1...v1.1.0-beta.2) (2026-11-18)


### Features

* show the detected callback URL and allow a per-entry override ([3c04e47](https://github.com/TheLuBu/ha-gira-one/commit/3c04e47123179ee04e417eb9a0abc8804c414363))


### Bug Fixes

* keep callback examples in the setup documentation ([9825b29](https://github.com/TheLuBu/ha-gira-one/commit/9825b290423cf84a1bcbaaee1648ed5e883f3bd9))

## [1.1.0-beta.1](https://github.com/TheLuBu/ha-gira-one/compare/v1.0.5...v1.1.0-beta.1) (2026-11-18)


### Features

* discover and recall numbered Gira scenes ([#4](https://github.com/TheLuBu/ha-gira-one/issues/4)) ([750f394](https://github.com/TheLuBu/ha-gira-one/commit/750f3947f8a0e17390a50c232a143ca404a110db))
* expose audio and status entities and surface rejected writes ([#14](https://github.com/TheLuBu/ha-gira-one/issues/14), [#18](https://github.com/TheLuBu/ha-gira-one/issues/18)-20) ([2732dc6](https://github.com/TheLuBu/ha-gira-one/commit/2732dc62eadc5ab7574c2d877f7bd5008e665ed3))
* expose configured Gira trigger buttons ([#7](https://github.com/TheLuBu/ha-gira-one/issues/7)) ([fb5afe2](https://github.com/TheLuBu/ha-gira-one/commit/fb5afe2ddb1222dc3f4b9bf2360c294cfb67e70b))
* support Gira REST API v3 with v2 fallback ([f6ca7bb](https://github.com/TheLuBu/ha-gira-one/commit/f6ca7bb34124d96d4cb97be808467fc17c15390d))
* use verified GPA pictograms for switches and scenes ([#5](https://github.com/TheLuBu/ha-gira-one/issues/5)) ([3d904ee](https://github.com/TheLuBu/ha-gira-one/commit/3d904ee7e6a2d6c20eca8a0f7c594131ceabbedb))
* verify callback delivery, reconcile restarts and compare project revisions ([#9](https://github.com/TheLuBu/ha-gira-one/issues/9), [#12](https://github.com/TheLuBu/ha-gira-one/issues/12)-13, [#15](https://github.com/TheLuBu/ha-gira-one/issues/15)-17) ([7251519](https://github.com/TheLuBu/ha-gira-one/commit/725151920e5d0274e19bb6972385e178a27c685d))


### Bug Fixes

* accept HTTPS IP callback URLs and retry cleanly ([ee0dc99](https://github.com/TheLuBu/ha-gira-one/commit/ee0dc997660dce15434cba19379f58b143950178))
* avoid retriggering switch-on brightness while dimming ([c3f162d](https://github.com/TheLuBu/ha-gira-one/commit/c3f162d6bf6bb793613e8822b35243f2c3fb9797))
* read datapoint states and await shutdown cleanup ([6fd6f89](https://github.com/TheLuBu/ha-gira-one/commit/6fd6f8912101a132413afdda5fb9d8d522b5c29e))
* reconcile entry states after callback delivery failures ([#2](https://github.com/TheLuBu/ha-gira-one/issues/2)) ([a29cc6c](https://github.com/TheLuBu/ha-gira-one/commit/a29cc6c0153b7ee2c6c88a1540b867d717855d6e))
* round Gira brightness feedback to the nearest HA step ([1e737f5](https://github.com/TheLuBu/ha-gira-one/commit/1e737f54293132494257e23d76ed0f5ebf211b32))
* support native RGB and RGBW commands and feedback on firmware 4 ([#6](https://github.com/TheLuBu/ha-gira-one/issues/6)) ([1ace4ca](https://github.com/TheLuBu/ha-gira-one/commit/1ace4ca566173b487d062cc47a6cb972290f6fdb))
* switch on at requested brightness without an extra ON command ([b86ef9c](https://github.com/TheLuBu/ha-gira-one/commit/b86ef9c49424f8088128d8de8f824b474f8105eb))


### Miscellaneous Chores

* enable prerelease tagging in the fork ([ee4c91c](https://github.com/TheLuBu/ha-gira-one/commit/ee4c91c9d15a8f8dc5ed1f810af50b782ddbce60))

## [1.0.5](https://github.com/tbsv/ha-gira-one/compare/v1.0.4...v1.0.5) (2026-09-06)


### Bug Fixes

* migrate via_device to via_device_id for HA 2026.8 ([85d61ae](https://github.com/tbsv/ha-gira-one/commit/85d61ae2f54f6f352c01fa8190b5dc8dc1af9ce4))
* migrate via_device to via_device_id for HA 2026.8 ([f1a0dd3](https://github.com/tbsv/ha-gira-one/commit/f1a0dd3e36123a6a883387e00300c038e3e4ee75))
