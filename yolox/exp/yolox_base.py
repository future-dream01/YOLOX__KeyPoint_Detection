#!/usr/bin/env python3
# -*- coding:utf-8 -*-
# Copyright (c) 2014-2021 Megvii Inc. All rights reserved.

import os
import random

import torch
import torch.distributed as dist
import torch.nn as nn

from .base_exp import BaseExp


class Exp(BaseExp):                 # 实验配置类
    def __init__(self):
        super().__init__()
        # ---------------- transfer learning config ---------------- #
        self.use_distillation  = False
        self.teacher_pth = "/home/rangeronmars/AI/TUP-NN-Train/teacher/teacher.pth" # 教师模型
        # ---------------- model config ---------------- #
        self.num_classes = 3        # 类别数，改动（8）
        #self.num_colors = 4         # 颜色数
        self.num_apexes = 4         # 特征点数，改动（4）
        self.depth = 1.00           # 网络深度因子
        self.width = 1.00           # 网络宽度因子
        self.act = 'hswish'         # 激活函数

        # ---------------- dataloader config ---------------- #
        # set worker to 4 for shorter dataloader init time
        self.data_num_workers = 8
        self.input_size = (640,640)  # 输入宽度
        # Actual multiscale ranges: [640-5*32, 640+5*32].
        # To disable multiscale training, set the
        # self.multiscale_range to 0.
        # self.multiscale_range = 5
        # You can uncomment this line to specify a multiscale range
        self.random_size = (10, 18)
        self.data_dir = "datasets/coco"
        self.train_ann = "instances_train2017.json"
        self.val_ann = "instances_val2017.json"

        # --------------- transform config ----------------- #
        #Mosaic
        self.mosaic_prob = 0.2          # 不使用mosaic增强
        self.mosaic_scale = (0.3, 2)
        #Mixup
        self.enable_mixup = False
        self.mixup_prob = 0.2          # 不适用mixup增强
        self.mixup_scale = (0.5, 1.5)
        #HSV
        self.hsv_prob = 1.0
        #Gaussian Blur
        self.noise_prob = 0.5
        #Flip 
        self.flip_prob = 0.0
        #Affine
        self.degrees = 20.0
        self.translate = 1.0
        self.shear = 1.0
        self.perspective = 1.0

        # --------------  training config --------------------- #
        #For Using SGD+Momentum
        self.warmup_epochs = 20
        self.max_epoch = 20            # 训练轮次数
        self.warmup_lr = 0
        self.basic_lr_per_img = 0.0001 # 每张图片的基础学习率
        self.scheduler = "yoloxwarmcos"
        self.no_aug_epochs = 200        # 最后的50个轮次不使用数据增强
        self.min_lr_ratio = 0.06       # 最小学习率比例
        self.ema = True

        self.weight_decay = 5e-4    # 权重衰减
        # self.weight_decay = 1e-8
        self.momentum = 0.9     

        self.print_interval = 10
        self.eval_interval = 10     # 每10个轮次进行一次评估
        self.per_class_AP = True    # 计算每个类别的AP
        self.per_class_AR = True    # 计算每个类别的AR
        self.save_history_ckpt = True  # 是否保存历史检查点
        self.exp_name = os.path.split(os.path.realpath(__file__))[1].split(".")[0]

        # -----------------  testing config ------------------ #
        self.test_size = (640,640)          # 输入尺寸
        self.test_conf = 0.25
        self.nmsthre = 0.3

    def get_model(self):                # 获取训练模型
        from yolox.models import YOLOX, YOLOPAFPN, YOLOXHead

        def init_yolo(M):
            for m in M.modules():
                if isinstance(m, nn.BatchNorm2d):
                    m.eps = 1e-3
                    m.momentum = 0.03
        if getattr(self, "model", None) is None:
            
            in_channels = [256, 512, 1024]
            backbone = YOLOPAFPN(self.depth, self.width, in_channels=in_channels, act=self.act)
            head = YOLOXHead(self.num_apexes,self.num_classes, self.width, in_channels=in_channels, act=self.act)
            self.model = YOLOX(backbone, head)

        self.model.apply(init_yolo)
        self.model.head.initialize_biases(1e-2)
        self.model.train()
        return self.model       # 返回模型

    # 创建数据加载器
    def get_data_loader(
        self, batch_size, is_distributed, no_aug=False, cache_img=False
    ):
        from yolox.data import (
            COCODataset,
            TrainTransform,
            YoloBatchSampler,
            DataLoader,
            InfiniteSampler,
            MosaicDetection,
            worker_init_reset_seed,
        )
        from yolox.utils import (
            wait_for_the_master,
            get_local_rank,
        )
        local_rank = get_local_rank()
        #Load datasets
        with wait_for_the_master(local_rank):
            dataset = COCODataset(
                num_classes=self.num_classes,
                num_apexes=self.num_apexes,
                data_dir=self.data_dir,
                json_file=self.train_ann,
                img_size=self.input_size,
                preproc=TrainTransform(
                    num_apexes = self.num_apexes,
                    max_labels=50,
                    flip_prob=self.flip_prob,
                    hsv_prob=self.hsv_prob,
                    noise_prob=self.noise_prob),
                cache=cache_img,
            )
        #TODO:Fix Mosaic Data Augmentation 
        dataset = MosaicDetection(
            dataset,
            mosaic=not no_aug,
            img_size=self.input_size,
            preproc=TrainTransform(
                num_apexes = self.num_apexes,
                max_labels=120,
                flip_prob=self.flip_prob,
                hsv_prob=self.hsv_prob,
                noise_prob=self.noise_prob),
            degrees=self.degrees,
            translate=self.translate,
            mosaic_scale=self.mosaic_scale,
            mixup_scale=self.mixup_scale,
            shear=self.shear,
            perspective=self.perspective,
            enable_mixup=self.enable_mixup,
            mosaic_prob=self.mosaic_prob,
            mixup_prob=self.mixup_prob,
        )

        self.dataset = dataset

        if is_distributed:
            batch_size = batch_size // dist.get_world_size()

        sampler = InfiniteSampler(len(self.dataset), seed=self.seed if self.seed else 0)

        batch_sampler = YoloBatchSampler(
            sampler=sampler,
            batch_size=batch_size,
            drop_last=False,
            mosaic=not no_aug,
        )

        dataloader_kwargs = {"num_workers": self.data_num_workers, "pin_memory": True}
        dataloader_kwargs["batch_sampler"] = batch_sampler

        # Make sure each process has different random seed, especially for 'fork' method.
        # Check https://github.com/pytorch/pytorch/issues/63311 for more details.
        dataloader_kwargs["worker_init_fn"] = worker_init_reset_seed
        
        train_loader = DataLoader(self.dataset, **dataloader_kwargs)

        return train_loader

    def random_resize(self, data_loader, epoch, rank, is_distributed):
        tensor = torch.LongTensor(2).cuda()

        if rank == 0:
            size_factor = self.input_size[1] * 1.0 / self.input_size[0]
            if not hasattr(self, 'random_size'):
                min_size = int(self.input_size[0] / 32) - self.multiscale_range
                max_size = int(self.input_size[0] / 32) + self.multiscale_range
                self.random_size = (min_size, max_size)
            size = random.randint(*self.random_size)
            size = (int(32 * size), 32 * int(size * size_factor))
            tensor[0] = size[0]
            tensor[1] = size[1]

        if is_distributed:
            dist.barrier()
            dist.broadcast(tensor, 0)

        input_size = (tensor[0].item(), tensor[1].item())
        return input_size
    
    # 获取输入图像和标签
    def preprocess(self, inputs, targets, tsize):
        scale_y = tsize[0] / self.input_size[0]
        scale_x = tsize[1] / self.input_size[1]
        if scale_x != 1 or scale_y != 1:
            inputs = nn.functional.interpolate(
                inputs, size=tsize, mode="bilinear", align_corners=False
            )
            targets[..., 2::2] = targets[..., 2::2] * scale_x
            targets[..., 3::2] = targets[..., 3::2] * scale_y
        return inputs, targets

    def get_optimizer(self, batch_size):
        if "optimizer" not in self.__dict__:
            if self.warmup_epochs > 0:
                lr = self.warmup_lr
            else:
                lr = self.basic_lr_per_img * batch_size

            pg0, pg1, pg2 = [], [], []  # optimizer parameter groups

            for k, v in self.model.named_modules():
                if hasattr(v, "bias") and isinstance(v.bias, nn.Parameter):
                    pg2.append(v.bias)  # biases
                if isinstance(v, nn.BatchNorm2d) or "bn" in k:
                    pg0.append(v.weight)  # no decay
                elif hasattr(v, "weight") and isinstance(v.weight, nn.Parameter):
                    pg1.append(v.weight)  # apply decay

            optimizer = torch.optim.SGD(
                pg0, lr=lr, momentum=self.momentum, nesterov=True
            )

            optimizer.add_param_group(
                {"params": pg1, "weight_decay": self.weight_decay}
            )  # add pg1 with weight_decay
            optimizer.add_param_group({"params": pg2})
            self.optimizer = optimizer

        return self.optimizer

    def get_lr_scheduler(self, lr, iters_per_epoch):
        from yolox.utils import LRScheduler

        scheduler = LRScheduler(
            self.scheduler,
            lr,
            iters_per_epoch,
            self.max_epoch,
            warmup_epochs=self.warmup_epochs,
            warmup_lr_start=self.warmup_lr,
            no_aug_epochs=self.no_aug_epochs,
            min_lr_ratio=self.min_lr_ratio,
        )
        return scheduler

    def get_eval_loader(self, batch_size, is_distributed, testdev=False, legacy=False):
        from yolox.data import COCODataset, ValTransform

        valdataset = COCODataset(
            num_classes=self.num_classes,
            num_apexes=self.num_apexes,
            data_dir=self.data_dir,
            json_file=self.val_ann if not testdev else "image_info_test-dev2017.json",
            name="images",
            img_size=self.test_size,
            preproc=ValTransform(legacy=legacy),
        )

        if is_distributed:
            batch_size = batch_size // dist.get_world_size()
            sampler = torch.utils.data.distributed.DistributedSampler(
                valdataset, shuffle=False
            )
        else:
            sampler = torch.utils.data.SequentialSampler(valdataset)

        dataloader_kwargs = {
            "num_workers": self.data_num_workers,
            "pin_memory": True,
            "sampler": sampler,
        }
        dataloader_kwargs["batch_size"] = batch_size
        val_loader = torch.utils.data.DataLoader(valdataset, **dataloader_kwargs)

        return val_loader
    
    # 获取评估器
    def get_evaluator(self, batch_size, is_distributed, testdev=False, legacy=False):
        """
        Get Evaluator for model.
        """
        from yolox.evaluators import COCOEvaluator

        val_loader = self.get_eval_loader(batch_size, is_distributed, testdev, legacy)
        evaluator = COCOEvaluator(
            dataloader=val_loader,
            img_size=self.test_size,
            confthre=self.test_conf,
            nmsthre=self.nmsthre,
            num_apexes=self.num_apexes,
            num_classes=self.num_classes,
            #num_colors=self.num_colors,
            testdev=testdev,
            per_class_AP=self.per_class_AP,
            per_class_AR=self.per_class_AR
        )
        return evaluator

    def get_trainer(self, args):
        from yolox.core import Trainer
        trainer = Trainer(self, args)
        # NOTE: trainer shouldn't be an attribute of exp object
        return trainer

    def eval(self, model, evaluator, is_distributed, half=False, return_outputs=False):
        return evaluator.evaluate(model, is_distributed, half, return_outputs=return_outputs)
