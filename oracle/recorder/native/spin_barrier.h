/* A tiny portable rendezvous for the Oracle P4 native targets.
 *
 * macOS has no `pthread_barrier_t`, so the concurrent scenarios share this
 * spin barrier instead of a POSIX one. It is a fixed participant count reached
 * exactly once per phase: it orders the phases of a scenario, and nothing else.
 * It never makes the transcript depend on the interleaving - the drivers print
 * accounting and per-thread records, never the interleaving itself.
 */
#ifndef EU4_ORACLE_RECORDER_NATIVE_SPIN_BARRIER_H
#define EU4_ORACLE_RECORDER_NATIVE_SPIN_BARRIER_H

#include <stdatomic.h>
#include <stdint.h>

typedef struct {
    _Atomic uint32_t arrived;
    uint32_t expected;
} spin_barrier;

static void barrier_wait(spin_barrier *barrier)
{
    atomic_fetch_add_explicit(&barrier->arrived, 1u, memory_order_acq_rel);
    while (atomic_load_explicit(&barrier->arrived, memory_order_acquire) < barrier->expected) {
        /* spin: the rendezvous is short and the thread count is fixed */
    }
}

#endif /* EU4_ORACLE_RECORDER_NATIVE_SPIN_BARRIER_H */
