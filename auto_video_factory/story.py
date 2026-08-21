from __future__ import annotations

from .models import Scene, StoryPlan


class TemplateStoryPlanner:
    """Offline V1 planner.

    It deliberately keeps the planning provider replaceable so an LLM can be
    added later without coupling rendering to a specific vendor.
    """

    _beats = (
        "Mọi chuyện bắt đầu khi {topic}. Từng bước chân nặng nề trên con đường phủ đầy tuyết trắng, không ai ngờ rằng biến cố cay đắng ngày hôm ấy lại chính là khởi đầu cho một huyền thoại.",
        "Giữa màn sương lạnh buốt giá của đỉnh núi cao, nhân vật chính nhận ra mình không còn đường lui. Hắn siết chặt thanh kiếm trong tay và kiên định bước về phía trước.",
        "Những người từng coi thường hắn vẫn đứng đằng xa xì xào bàn tán, nhưng một luồng linh khí kỳ lạ bỗng nhiên cuộn trào mãnh liệt từ sâu bên trong kinh mạch.",
        "Khi nguy hiểm cận kề ập tới trong chớp mắt, sức mạnh ngủ quên bấy lâu bỗng thức tỉnh, tỏa ra hào quang rực rỡ khiến cả không gian xung quanh như ngưng đọng lại.",
        "Hắn không hề nóng vội trả thù những kẻ từng hãm hại mình. Trước tiên, hắn chọn cách ẩn nhẫn, tĩnh tâm để thấu hiểu tường tận nguồn sức mạnh vô tận mới khai mở.",
        "Một đối thủ hùng mạnh bậc nhất bất ngờ xuất hiện chắn ngang lối đi, buộc hắn phải đưa ra lựa chọn sinh tử giữa việc lùi bước hay dũng cảm đối mặt với quá khứ nghiệt ngã.",
        "Chỉ trong một khoảnh khắc ngắn ngủi, toàn bộ thế cục hoàn toàn đảo ngược. Những kẻ từng lớn tiếng chế giễu bắt đầu run sợ nhận ra mình đã đánh giá sai người.",
        "Nhưng tất cả điều này mới chỉ là bước khởi đầu gian nan. Phía sau cánh cổng sơn môn thâm u vẫn còn một bí mật kinh thiên động địa đang chờ được giải mã.",
        "Tin tức chấn động nhanh chóng lan truyền khắp muôn nơi, kéo theo những thế lực cổ xưa vốn ẩn mình bắt đầu hướng sự chú ý về cái tên tưởng chừng đã bị lãng quên.",
        "Đứng trước cánh cửa phong ấn cuối cùng, hắn hiểu sâu sắc rằng thử thách thực sự không phải là đối thủ trước mắt, mà là quyết định mang tính vận mệnh sắp được đưa ra.",
        "Một quyết định bất ngờ và táo bạo khiến tất cả mọi người đều sững sờ kinh ngạc, đồng thời chính thức mở ra một con đường xán lạn mà xưa nay chưa từng có ai dám bước.",
        "Câu chuyện tạm dừng lại ở khúc quanh định mệnh ấy, ngay trước khi cánh cửa dẫn tới bí mật lớn nhất của toàn cõi thiên hạ chuẩn bị hé mở trong hồi tiếp theo.",
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
