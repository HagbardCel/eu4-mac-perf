#ifndef EU4_SPSC_H
#define EU4_SPSC_H
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
/* One producer and one consumer per queue. Payload publication is release/acquire;
   consumer releases storage only after copying it. Never share a producer head. */
typedef struct { _Atomic uint64_t head,tail; } Eu4Spsc;
static inline bool eu4_spsc_reserve(Eu4Spsc *q,unsigned capacity,uint64_t *position) {
    *position=atomic_load_explicit(&q->head,memory_order_relaxed);
    return *position-atomic_load_explicit(&q->tail,memory_order_acquire)<capacity;
}
static inline void eu4_spsc_publish(Eu4Spsc *q,uint64_t position) {
    atomic_store_explicit(&q->head,position+1,memory_order_release);
}
static inline bool eu4_spsc_read(Eu4Spsc *q,uint64_t *position) {
    *position=atomic_load_explicit(&q->tail,memory_order_relaxed);
    return *position<atomic_load_explicit(&q->head,memory_order_acquire);
}
static inline void eu4_spsc_release(Eu4Spsc *q,uint64_t position) {
    atomic_store_explicit(&q->tail,position+1,memory_order_release);
}
#endif
