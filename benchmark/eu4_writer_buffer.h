#ifndef EU4_WRITER_BUFFER_H
#define EU4_WRITER_BUFFER_H
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <sys/types.h>
#define EU4_WRITER_BYTES (128u*1024u)
typedef struct { char data[EU4_WRITER_BYTES]; size_t used; uint64_t flushed_ns; bool failed; } Eu4WriterBuffer;
typedef ssize_t (*Eu4WriteFn)(void *,const char *,size_t);
static inline bool eu4_writer_flush(Eu4WriterBuffer *b,Eu4WriteFn write_fn,void *arg,uint64_t now) {
    size_t sent=0;
    if(b->failed) return false;
    while(sent<b->used) {
        ssize_t n=write_fn(arg,b->data+sent,b->used-sent);
        if(n<0 && errno==EINTR) continue;
        if(n<=0 || (size_t)n>b->used-sent) {b->failed=true;return false;}
        sent+=(size_t)n;
    }
    b->used=0;b->flushed_ns=now;return true;
}
static inline bool eu4_writer_append(Eu4WriterBuffer *b,Eu4WriteFn write_fn,void *arg,
                                     const char *data,size_t size,uint64_t now) {
    while(size && !b->failed) {
        size_t room=EU4_WRITER_BYTES-b->used;
        size_t n=size<room?size:room;
        memcpy(b->data+b->used,data,n);b->used+=n;data+=n;size-=n;
        if(b->used==EU4_WRITER_BYTES && !eu4_writer_flush(b,write_fn,arg,now)) return false;
    }
    return !b->failed;
}
static inline bool eu4_writer_tick(Eu4WriterBuffer *b,Eu4WriteFn fn,void *arg,uint64_t now) {
    return !b->failed && (!b->used || now-b->flushed_ns<10000000ull || eu4_writer_flush(b,fn,arg,now));
}
#endif
