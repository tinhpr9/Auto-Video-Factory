from __future__ import annotations

DURATION_TO_SCENES = {45: 6, 60: 8, 90: 12}

STYLE_OPTIONS = {
    "xianxia-cinematic": {
        "label": "Tiên hiệp điện ảnh",
        "prompt": (
            "Original cinematic xianxia-inspired fantasy illustration, cold cyan and slate palette, "
            "misty mountains, dramatic soft light, detailed painterly realism, vertical short-video frame"
        ),
    },
    "ink-wash": {
        "label": "Thủy mặc huyền ảo",
        "prompt": (
            "Original ink-wash inspired fantasy illustration, expressive brush texture, pale mist, "
            "restrained monochrome and cyan accents, elegant vertical composition"
        ),
    },
    "dark-fantasy": {
        "label": "Huyền ảo u tối",
        "prompt": (
            "Original dark fantasy illustration, moonlit blue-black palette, atmospheric fog, "
            "dramatic rim light, detailed cinematic vertical composition, non-graphic"
        ),
    },
}

VOICE_OPTIONS = {
    "marin": "Marin — kể chuyện tự nhiên",
    "onyx": "Onyx — giọng trầm",
}
