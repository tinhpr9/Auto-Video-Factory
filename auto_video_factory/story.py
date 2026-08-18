from __future__ import annotations

from .models import Scene, StoryPlan


class TemplateStoryPlanner:
    """Offline V1 planner.

    It deliberately keeps the planning provider replaceable so an LLM can be
    added later without coupling rendering to a specific vendor.
    """

    _beats = (
        "Mọi chuyện bắt đầu khi {topic}. Không ai nghĩ biến cố nhỏ ấy sẽ đổi cả số phận.",
        "Giữa màn sương lạnh, nhân vật chính nhận ra mình không còn đường lùi và buộc phải tiến lên.",
        "Những người từng coi thường hắn vẫn đứng phía sau, nhưng một dấu hiệu kỳ lạ đã xuất hiện trong cơ thể.",
        "Khi nguy hiểm ập tới, sức mạnh ngủ quên bỗng thức tỉnh, khiến cả không gian xung quanh như ngừng lại.",
        "Hắn không vội trả thù. Trước tiên, hắn giữ im lặng và tìm cách hiểu sức mạnh mới của mình.",
        "Một đối thủ mạnh hơn xuất hiện, buộc hắn phải lựa chọn giữa bỏ chạy và đối mặt với quá khứ.",
        "Chỉ trong một khoảnh khắc, thế cục đảo ngược. Những kẻ từng chế giễu bắt đầu nhận ra mình đã nhìn nhầm người.",
        "Nhưng đây mới chỉ là khởi đầu. Phía sau sơn môn vẫn còn một bí mật lớn hơn đang chờ được mở ra.",
        "Tin tức lan khắp nơi, kéo theo những thế lực vốn im lặng bắt đầu chú ý tới cái tên tưởng đã bị lãng quên.",
        "Trước cánh cửa cuối cùng, hắn hiểu rằng thử thách thật sự không phải kẻ thù, mà là lựa chọn mình sắp đưa ra.",
        "Một quyết định bất ngờ khiến mọi người sững sờ, đồng thời mở ra con đường mà chưa ai dám bước.",
        "Câu chuyện dừng lại ở đó, ngay trước khi bí mật lớn nhất được hé lộ trong phần tiếp theo.",
    )

    _visuals = (
        "generic xianxia wanderer in a snowy mountain valley, pale cyan mist, cinematic vertical composition",
        "ancient sect gate in snow, lone traveler, cold blue atmosphere, wide cinematic shot",
        "quiet courtyard, surprised disciples in the distance, winter haze, original fantasy characters",
        "mystical energy awakening around a lone swordsman, snow particles, luminous cyan light",
        "solitary cultivation room, calm swordsman studying faint spiritual light, cold moonlight",
        "powerful rival arriving at a snowy stone bridge, tense fantasy atmosphere, original characters",
        "dramatic confrontation resolved in one strike, flowing robes, snow and mist, non-graphic",
        "ancient sealed hall beyond the mountain sect, mysterious symbols, cyan fog, cinematic",
        "distant mountain clans receiving news, scrolls and lanterns, winter fantasy atmosphere",
        "large ancient door before a lone swordsman, high contrast light, vertical composition",
        "unexpected choice at a cliffside temple, calm determined figure, cold dawn",
        "mysterious silhouette beyond clouds, cliffhanger composition, pale cyan fantasy landscape",
    )

    def __init__(self, scene_count: int = 8) -> None:
        if not 2 <= scene_count <= 12:
            raise ValueError("scene_count must be between 2 and 12")
        self.scene_count = scene_count

    def plan(self, topic: str) -> StoryPlan:
        topic = " ".join(topic.split()).strip()
        if not topic:
            raise ValueError("topic must not be empty")
        scenes: list[Scene] = []
        for idx in range(self.scene_count):
            narration = self._beats[idx].format(topic=topic)
            scenes.append(
                Scene(index=idx + 1, narration=narration, visual_prompt=self._visuals[idx])
            )
        return StoryPlan(title=f"{topic} — truyện ngắn", scenes=scenes)
