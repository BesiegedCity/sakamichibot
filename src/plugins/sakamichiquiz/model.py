from datetime import datetime
from enum import IntEnum
from typing import Optional

import nonebot
from nonebot.adapters.cqhttp import MessageSegment
from pydantic import BaseModel

from .config import Config

global_config = nonebot.get_driver().config
plugin_config = Config(**global_config.dict())
IMAGECATAGORY = plugin_config.qna_imagecatagory
MUSICCATAGORY = plugin_config.qna_musiccatagory


class QuestionType(IntEnum):
    text = 1
    music = 2
    image = 3
    identify = 4


class QuestionGroup(IntEnum):
    nogi = 1
    hina = 2
    saku = 3


class Question(BaseModel):
    """This class is used to new a Question Object.
    """
    id: Optional[int]  # Automatically get from retVal of SQLServer.
    typ: QuestionType = QuestionType.text
    content: str
    answers: str  # Answers should be all text format divided by '|' without any record or image referred.
    files: Optional[str] = None
    analysis: Optional[str] = None
    analfiles: Optional[str] = None
    author: int = 10000  # QQ account
    countright: int = 0  # Number of answering the question correctly
    counttotal: int = 0  # Answered times in total
    sakagroup: QuestionGroup
    if_err: Optional[int] = 0  # Identify question with no pic/music(it should attach)
    create_time: Optional[datetime]  # Import timestamp, set automatically by MySQL

    def accuracy(self):
        if self.counttotal != 0:
            return "%.2f" % (self.countright / self.counttotal * 100)
        else:
            return "-"

    def message_question(self):
        msg = MessageSegment("text", {"text": f"No.{self.id}"}) + "\n"
        msg = msg + MessageSegment("text", {"text": f"{self.content}"})
        if self.files is not None:
            msg = msg + self._split_files(self.files)
        return msg

    def message_analysis(self):
        msg = MessageSegment("text", {"text": f"No.{self.id}"}) + "\n"
        msg = msg + MessageSegment("text", {"text": "解析："}) + MessageSegment("text", {"text": f"{self.analysis}"})
        if self.analfiles is not None:
            msg = msg + self._split_files(self.analfiles)
        return msg

    def message_all(self):
        msg = MessageSegment("text", {"text": "序号："}) + MessageSegment("text", {"text": f"{self.id}"}) + "\n"
        msg = msg + MessageSegment("text", {"text": "类型："}) + MessageSegment("text",
                                                                             {"text": f"{str(self.typ)}"}) + "\n"
        msg = msg + MessageSegment("text", {"text": "题目："}) + MessageSegment("text", {"text": f"{self.content}"}) + "\n"
        if self.files is not None:
            msg = msg + self._split_files(self.files) + "\n"
        msg = msg + MessageSegment("text", {"text": "答案："}) + MessageSegment("text", {"text": f"{self.answers}"}) + "\n"
        msg = msg + MessageSegment("text", {"text": "解析："}) + MessageSegment("text",
                                                                             {"text": f"{self.analysis}"}) + "\n"
        if self.analfiles is not None:
            msg = msg + self._split_files(self.analfiles) + "\n"
        msg = msg + MessageSegment("text", {"text": "作者："}) + MessageSegment("text", {"text": f"{self.author}"}) + "\n"
        msg = msg + MessageSegment("text", {"text": "正确率："}) + MessageSegment("text",
                                                                              {"text": f"{self.accuracy()}"}) + "\n"
        msg = msg + MessageSegment("text", {"text": "组别："}) + MessageSegment("text",
                                                                             {"text": f"{str(self.sakagroup)}"}) + "\n"
        msg = msg + MessageSegment("text", {"text": "是否出错："}) + MessageSegment("text",
                                                                               {"text": f"{self.if_err}"}) + "\n"
        msg = msg + MessageSegment("text", {"text": "创建时间："}) + MessageSegment("text", {"text": f"{self.create_time}"})

        return msg

    @staticmethod
    def _split_files(raw: str):
        ls = raw.split("|")
        msg = None
        for val in ls:
            if val.find("jpg") != -1 or val.find("png") != -1 or val.find("gif") != -1 or val.find("jpeg") != -1:
                msg = msg + MessageSegment.image(IMAGECATAGORY.joinpath(val))
            if val.find("mp3") != -1:
                msg = msg + MessageSegment.record(MUSICCATAGORY.joinpath(val))

        return msg
