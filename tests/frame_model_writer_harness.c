#include "../benchmark/eu4_writer_buffer.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct { char *data;size_t size,capacity,calls;bool interrupt,fail; } Sink;
static ssize_t write_sink(void *arg,const char *data,size_t size) {
    Sink *s=arg;s->calls++;
    if(s->interrupt) {s->interrupt=false;errno=EINTR;return -1;}
    if(s->fail) {errno=EIO;return -1;}
    if(size>701) size=701;
    assert(s->size+size<=s->capacity);
    memcpy(s->data+s->size,data,size);s->size+=size;return (ssize_t)size;
}
int main(void) {
    Eu4WriterBuffer b={0};size_t size=EU4_WRITER_BYTES*3+19;
    char *input=malloc(size),*output=malloc(size);assert(input && output);
    for(size_t i=0;i<size;i++) input[i]=(char)(i%127);
    Sink sink={.data=output,.capacity=size,.interrupt=true};
    assert(eu4_writer_append(&b,write_sink,&sink,input,size,1));
    assert(b.used==19 && sink.size==size-19);
    assert(eu4_writer_tick(&b,write_sink,&sink,9999999));assert(b.used==19);
    assert(eu4_writer_tick(&b,write_sink,&sink,10000001));assert(!b.used && sink.size==size);
    assert(!memcmp(input,output,size));
    b=(Eu4WriterBuffer){0};sink.size=0;
    assert(eu4_writer_append(&b,write_sink,&sink,"F,1\n",4,0));
    assert(eu4_writer_flush(&b,write_sink,&sink,0)); /* Immediate shutdown. */
    assert(sink.size==4 && !memcmp(output,"F,1\n",4));
    sink.fail=true;assert(eu4_writer_append(&b,write_sink,&sink,"Z\n",2,0));
    assert(!eu4_writer_flush(&b,write_sink,&sink,0) && b.failed);
    size_t calls=sink.calls;assert(!eu4_writer_append(&b,write_sink,&sink,"X\n",2,0));assert(calls==sink.calls);
    free(input);free(output);puts("batched boundaries, 10ms flush, short writes, EINTR, failure and shutdown passed");
}
