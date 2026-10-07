#pragma once

#include <stdatomic.h>

extern _Atomic unsigned char g_eu4_border_observer_active;

#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
_Static_assert(ATOMIC_CHAR_LOCK_FREE == 2, "gateway fast-path requires lock-free char atomic");
#endif
