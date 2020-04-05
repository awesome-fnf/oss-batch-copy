# -*- coding: utf-8 -*-
import os
import logging
import oss2
import json
from datetime import datetime
from task_queue import TaskQueue
import threading

LOG = logging.getLogger()
log_file_path = "/tmp/log.log"
oss2.set_file_logger(log_file_path, 'oss2', logging.WARNING)

class BatchProcessError(Exception):
  pass

def handler(event, context):
    evt = json.loads(event)
    oss_client = get_oss_client(evt, context)
    # return build_index(evt, oss_client)
    return copy_objects(evt, oss_client)

def copy_objects(evt, oss_client):
    global num_files_copied
    global exception

    num_files_copied = 0
    exception = None
    lock = threading.Lock()
    
    index_file_key = evt['index_file_key']
    object_meta = oss_client.get_object_meta(index_file_key)

    lines, next_offset = get_objects_to_copy(evt, oss_client, object_meta)

    if next_offset >= object_meta.content_length:
        next_offset = -1
    def producer(queue):
        for line in lines:
            oss_object = json.loads(line)
            LOG.info("Producing line %s", line)
            queue.put({
                "oss_object": oss_object
            })

    def consumer(queue):
        global num_files_copied
        global exception
        while queue.ok():
            with lock:
                if exception:
                    LOG.error("Exiting consumer loop due to an existing exception: {}".format(exception))
                    return
            
            item = queue.get()
            if item is None:
                LOG.debug("Consumer break")
                break
            
            try:
                headers={}
                oss_object = item['oss_object']
                src_oss_key = oss_object['key']
                dest_prefix = evt['dest_prefix']
                storage_class = evt['storage_class']

                if storage_class != "":
                    # Change storage class during CopyObject if storage class is specified
                    headers["x-oss-storage-class"] = storage_class

                bucket = evt['bucket']
                dest_oss_key = os.path.join(dest_prefix, src_oss_key)

                oss_client.copy_object(bucket, src_oss_key, dest_oss_key, headers=headers)
                with lock:
                    num_files_copied += 1
            except Exception as e:
                with lock:
                    if exception:
                        LOG.error("Exiting consumer loop due to an existing exception: {}, current exception: {}".format(exception, e))
                        return
                    exception = e
                LOG.exception(e)

    task_q = TaskQueue(producer, [consumer] * 16)
    task_q.run()

    if exception:
        LOG.error("Batch failed due to loop due to {}".format(exception))
        raise BatchProcessError(exception)

    fct = evt['files_copied_total']
    progress = next_offset*100/object_meta.content_length
    if next_offset == -1:
        progress = 100
    LOG.info("next offset %d\n", next_offset)
    return {"offset_bytes": next_offset, "files_copied_total": fct + num_files_copied, "files_copied_in_batch": num_files_copied, "progress_pct": "{0:.2f}%".format(progress)}

def get_objects_to_copy(evt, oss_client, index_file_meta):
    offset = evt['offset_bytes']
    chunk_bytes = evt['chunk_bytes']
    index_file_key = evt['index_file_key']

    content_length = index_file_meta.content_length

    lines = []
    end_offset = offset + chunk_bytes - 1
    next_offset = -1
    if offset >= content_length:
        return lines, next_offset

    while True:
        if end_offset >= content_length:
            end_offset = content_length - 1

        result = oss_client.get_object(index_file_key, byte_range=(offset, end_offset))
        data = result.read()
        last_new_line_index = data.rfind(b'\n')

        if last_new_line_index == -1:
            if end_offset >= content_length:
                # Reached the end of the index file, no new line found, the data is treated a single line
                lines.append(data)
                next_offset = -1
                break
            # keep finding until at least one new line is found or reached the end of the index file
            end_offset = end_offset + chunk_bytes
            continue

        truncated_data = data[0:last_new_line_index]
        lines = truncated_data.split(b'\n')
        next_offset = offset + last_new_line_index + 1
        break

    return lines, next_offset

def build_index(evt, oss_client):
    src_prefix = evt['src_prefix']
    index_file_path = '/tmp/index.txt'
    file_object = open(index_file_path, 'a')

    for oi in oss2.ObjectIterator(oss_client, prefix=src_prefix, max_keys=1000, marker=''):
        line = json.dumps(oi.__dict__) + '\n'
        file_object.write(line)

    file_object.close()
    oss_client.put_object_from_file('oss-copy/index.txt', index_file_path)

def get_oss_client(evt, context):
    region = evt['region']
    bucket = evt['bucket']
    creds = context.credentials
    auth = oss2.StsAuth(creds.accessKeyId, creds.accessKeySecret, creds.securityToken)
    oss_client = oss2.Bucket(auth, 'oss-' + region + '.aliyuncs.com', bucket)
    return oss_client