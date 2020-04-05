## 应用介绍
该应用模板通过 Serverless 工作流 (FnF) 和 函数计算（FC）支持亿级别（OSS 文件数量）批量 CopyObject. 工作流不断地调用 `copy_files` FC 函数，每次调用传入当前索引文件分页信息，获取索引中的一部分文件，并行向 OSS 目标路径复制。每批 `copy_files` 结束后会检查是否还有更多的文件需要被处理，如果有，检查当前流程 event 是否过多（例如超过 4000）如果没有过多则继续执行 `copy_files` ，否则开启一个新的流程执行，`start_sub_flow_execution` 步骤会等待子流程结束后结束整个流程。如果文件数非常多，有可能会有多次子流程执行形成一个递归的效果。

![](images/flow.png)

## 开发流程

```bash
# Clone project 后
cd oss-batch-copy
fun deploy
```

## 启动流程
在 [Serverless 控制台](https://fnf.console.aliyun.com/) 进入新创建的 copy_objects 流程，并且使用下面的 json 对象作为输入，注意替换

```json
{
  "region": "cn-hangzhou",
  "bucket": "fnf-demo-cn-hangzhou",
  "dest_prefix": "oss-copy/copied",
  "index_file_key": "oss-copy/index.txt",
  "offset_bytes": 0,
  "chunk_bytes": 1000000,
  "files_copied_total": 0,
  "storage_class": "Archive"
}
```

* dest_prefix: 假设 dest_prefix 为 d/e/f, 源 OSS 文件 key 为 a/b/c.txt copy 后的 OSS key 为 d/e/f/a/b/c.txt
* index_file_key: 存放需要被拷贝的索引文件的 OSS key 
* offset_bytes: 如果索引文件过大则需要分页处理。分页读取索引索引文件的 offset。如果 offset_bytes = 0 则从头开始处理所有索引中的文件。如流程出异常失败，可以从取失败步骤输入的 offset_bytes 作为一个新的执行输入，跳过已经成功的拷贝
* chunk_bytes: 每页的长度。每页会裁减掉最后一个 '\n' 后的内容。例如当前页的内容是 "abd.txt\nrfg.txt\nxyz" 实际看到的是 2 行 abd.txt 和 nrfg.txt。最后一个 '\n' 后的 nxyz 会被忽略