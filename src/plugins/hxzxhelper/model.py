from io import BytesIO, BufferedIOBase
from math import ceil
from typing import List, Optional, Union

import emoji
import nonebot
from PIL import Image, ImageFont, ImageDraw
from nonebot.adapters.cqhttp.message import MessageSegment
from nonebot.log import logger
from pydantic import BaseModel

from .config import Config

mailcnt = 0
global_config = nonebot.get_driver().config
plugin_config = Config(**global_config.dict())


def square_n_thumb(imglist: list, sidelength: int) -> list:
    """
    将图片裁减为方形，并生成缩略图

    :param imglist: 存放图片的List
    :param sidelength: 缩略图需要的边长
    :text_crurn: 修改后的图片存放的List
    """
    for i in range(len(imglist)):
        min_edge = min(imglist[i].size[0], imglist[i].size[1])
        cut_width = (imglist[i].size[0] - min_edge) / 2
        cut_height = (imglist[i].size[1] - min_edge) / 2
        imglist[i] = imglist[i].crop((cut_width, cut_height,
                                      imglist[i].size[0] - cut_width, imglist[i].size[1] - cut_height))
        imglist[i].thumbnail((sidelength, sidelength))
    return imglist


class Mail(object):
    no: int
    raw_text: str
    images: Union[List[bytes], List[BufferedIOBase]]
    translation: str
    time: str

    def __init__(self):
        global mailcnt

        self.no = mailcnt
        self.raw_text = ""
        self.images = []
        self.translation = ""
        self.time = ""
        self.stat = 0  # 0: 初始状态/非mail内容 1：图片载入完成，等待翻译 2：翻译载入完成，等待图片 3：等待发送
        self.type = ""  # "tweet" "mail"

        mailcnt += 1

    def status(self) -> str:
        if self.stat == 0:
            return "非mail内容，等待发送"
        if self.stat == 1:
            return "图片收集完成，等待翻译"
        if self.stat == 2:
            return "翻译收集完成，等待图片"
        if self.stat == 3:
            return "准备完毕，等待发送"

    def message(self):
        msg = MessageSegment.text(self.translation)
        if self.images:
            for image in self.images:
                msg += MessageSegment.image(image)
        return msg

    def info(self):
        msg = MessageSegment.text("序号：" + str(self.no) + "\n")
        msg += MessageSegment.text(f"类型：{self.type}\n")
        msg += MessageSegment.text("原文：\n")
        if self.raw_text:
            msg += MessageSegment.text("*************\n")
            msg += MessageSegment.text(self.raw_text)
            msg += MessageSegment.text("\n*************\n")
        msg += MessageSegment.text("图片：")
        if self.images:
            for image in self.images:
                msg += MessageSegment.image(image)
        msg += MessageSegment.text("\n")
        msg += MessageSegment.text("翻译：\n")
        if self.translation:
            msg += MessageSegment.text("*************\n")
            msg += MessageSegment.text(self.translation)
            msg += MessageSegment.text("\n*************\n")
        msg += MessageSegment.text("状态：" + self.status())
        return msg

    def imgcreate(self):
        top = Image.open("./imgsrc/top.jpg")
        bottom = Image.open("./imgsrc/bottom.jpg")
        background = Image.open("./imgsrc/background.jpg")
        fnt = ImageFont.truetype("./imgsrc/font.otf", 45)
        fnt_emoji = ImageFont.truetype("./imgsrc/font_emoji.ttf", 109, layout_engine=ImageFont.LAYOUT_RAQM)
        width = background.size[0]
        height_top = top.size[1]
        height_bottom = bottom.size[1]
        d = ImageDraw.Draw(background)
        imgs = []
        if self.images:
            imgs = [Image.open(BytesIO(img)) for img in self.images]
        s = self.translation

        def emoji_repl(symbol, meta):
            return symbol

        s = emoji.replace_emoji(s, emoji_repl)

        text_edited = ""
        text_tmp = ""
        for char in list(s.replace("\r\n", "\n").replace("　", "")):
            text_tmp += char
            if char == "\n":
                text_edited += text_tmp
                text_size = 0
                text_tmp = ""
            else:
                text_size = d.textsize(text_tmp, font=fnt)[0]
            if text_size > width - 30 * 4:
                text_edited += text_tmp + "\n"
                text_size = 0
                text_tmp = ""
        if text_tmp != "":
            text_edited += text_tmp

        font_height = d.textsize(plugin_config.dynamic_topic, font=fnt)[1]  # 设置首行话题的高度
        height_text = (font_height + 30) * (1 + len(text_edited.split("\n")))
        logger.debug(f"预设文字高度：{height_text}")
        text_ground = Image.new("RGB", size=(width, height_text), color=(255, 255, 255))
        d = ImageDraw.Draw(text_ground)
        d.text((30, 0), plugin_config.dynamic_topic, font=fnt, fill=(17, 136, 178))  # 行距30，左边距30

        width_offset, height_offset = (30, font_height + 25)
        r = emoji.get_emoji_regexp()
        for line in text_edited.split("\n"):
            if not line:
                height_offset += font_height + 30
                width_offset = 30
                continue
            line_split_emj = r.split(line)
            for text in line_split_emj:
                if not emoji.is_emoji(text):
                    d.text((width_offset, height_offset), text, font=fnt, fill=(0, 0, 0))
                    width_offset += int(fnt.getlength(text))
                else:
                    t = Image.new("RGB", size=(150, 150), color=(255, 255, 255))  # FreeType 不可以直接设定尺寸，只能手动缩放
                    td = ImageDraw.Draw(t)
                    td.text((0, 20), text, font=fnt_emoji, fill=(0, 0, 0), embedded_color=True)
                    t = t.resize((60, 60))
                    text_ground.paste(t, (width_offset, height_offset))
                    width_offset += 55
            height_offset += font_height + 30
            width_offset = 30
        logger.debug(f"最终文字高度：{height_offset}")

        height_pic = background.size[1]
        pic_ground = background.copy()  # 没有图片的时候只粘贴一段默认空白背景
        if imgs:
            if len(imgs) == 1:
                sidelen = width - 30 * 2
                rate = sidelen / imgs[0].size[0]
                imgs[0] = imgs[0].resize((int(rate * imgs[0].size[0]), int(rate * imgs[0].size[1])))
                height_pic = imgs[0].size[1]
                pic_ground = Image.new("RGB", size=(width, height_pic), color=(255, 255, 255))
                pic_ground.paste(imgs[0], box=(30, 0))
            else:
                if len(imgs) == 2:
                    sidelen = round((width - 30 * 2 - 15) / 2)
                    height_pic = sidelen
                    imgs = square_n_thumb(imgs, sidelen)
                    pic_ground = Image.new("RGB", size=(width, height_pic), color=(255, 255, 255))
                    pic_ground.paste(imgs[0], box=(30, 0))
                    pic_ground.paste(imgs[1], box=(30 + sidelen + 15, 0))
                else:
                    sidelen = round((width - 30 * 2 - 15 * 2) / 3)
                    height_pic = (sidelen + 15) * ceil(len(imgs) / 3) - 15
                    imgs = square_n_thumb(imgs, sidelen)
                    pic_ground = Image.new("RGB", size=(width, height_pic), color=(255, 255, 255))

                    column_cursor = 0
                    row_cursor = - (sidelen + 15)
                    text_cnt = 1
                    for img in imgs:
                        if text_cnt % 3 == 1:
                            column_cursor = 30
                            row_cursor += sidelen + 15
                        else:
                            column_cursor += sidelen + 15
                        pic_ground.paste(img, box=(column_cursor, row_cursor))
                        text_cnt = text_cnt + 1
                        if text_cnt > 9:
                            break

        height_total = height_top + height_text + height_pic + height_bottom

        final = Image.new("RGB", (width, height_total))  # 前面计算需要多少高度，并准备好图片的四个部分（top/text/pic/bottom）
        final.paste(top, box=(0, 0))
        final.paste(text_ground, box=(0, height_top))
        final.paste(pic_ground, box=(0, height_top + height_text))
        final.paste(bottom, box=(0, height_top + height_text + height_pic))
        ret = BytesIO()
        final.save(ret, format="jpeg")
        return ret.getvalue()

    def preview(self):
        notes1 = "\n—————————\n" \
                 f"*如需修改请重新发送翻译，无需取消，旧翻译会被覆盖\n" \
                 f"**发送“取消发送 {self.no}”取消"
        notes2 = "\n—————————\n" \
                 f"*如需修改请先取消发送，再重新回复原消息\n" \
                 f"**发送“取消发送 {self.no}”取消"
        if self.stat != 0:
            # msg = "【发送预览】\n#贺喜遥香#\n" + self.message() + notes1
            msg = "【发送预览】\n-检查翻译错误/图片缺失情况-\n" + MessageSegment.image(self.imgcreate()) + notes1
        else:
            # msg = "【发送预览】\n#贺喜遥香#\n" + self.message() + notes2
            msg = "【发送预览】\n-检查翻译错误/图片缺失情况-\n" + MessageSegment.image(self.imgcreate()) + notes2
        return msg


class ParsedObject(BaseModel):
    text: str
    images_url: List[str]
    timestamp: Optional[str]
