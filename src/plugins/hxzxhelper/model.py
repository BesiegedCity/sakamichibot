from io import BytesIO
from math import ceil
from typing import List

import nonebot
from PIL import Image, ImageFont, ImageDraw
from nonebot.adapters.cqhttp.message import MessageSegment

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
    images: List[bytes]
    translation: str
    time: int

    def __init__(self):
        global mailcnt

        self.no = mailcnt
        self.raw_text = ""
        self.images = []
        self.translation = ""
        self.time = -1
        self.stat = 0  # 0: 初始状态/非mail内容 1：图片载入完成，等待翻译 2：翻译载入完成，等待图片 3：等待发送

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
        width = background.size[0]
        height_top = top.size[1]
        height_bottom = bottom.size[1]
        d = ImageDraw.Draw(background)

        imgs = [Image.open(BytesIO(img)) for img in self.images]
        s = self.translation

        text_cr = ""
        text_cnt = 0
        for char in list(s.replace("\r\n", "\n")):
            if char == "\n":
                text_cnt = 0
            else:
                text_cnt = text_cnt + 1
            if text_cnt > 20:  # 一行最多21个汉字（不包含任何符号）
                text_cr = text_cr + "\n"
                text_cnt = 0
            text_cr = text_cr + char
        height_headline = d.textsize(plugin_config.dynamic_topic, font=fnt)[1]  # 设置首行话题的高度
        height_text = height_headline + 25 + d.multiline_textsize(text_cr, font=fnt, spacing=30)[1] + 60  # 预留60像素
        text_ground = Image.new("RGB", size=(width, height_text), color=(255, 255, 255))
        d = ImageDraw.Draw(text_ground)
        d.text((30, 0), plugin_config.dynamic_topic, font=fnt, fill=(17, 136, 178))  # 行距30，左边距30
        d.multiline_text((30, height_headline + 25), text_cr, spacing=30, font=fnt,
                         fill=(0, 0, 0))  # 计算下一行开始的时候要考虑行距+字高

        height_pic = background.size[1]
        pic_ground = background.copy()  # 没有图片的时候只粘贴一段默认空白背景
        if imgs:
            if len(imgs) == 1:
                sidelen = width - 30 * 2
                imgs[0].thumbnail((sidelen, sidelen))
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
