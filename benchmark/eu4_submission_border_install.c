#include <stdbool.h>

#include "eu4_submission_border.h"
#include "eu4_submission_border_loop_head.h"

bool eu4_submission_try_install_all_hooks(void) {
    eu4_border_init_gl_apis();
    if (!eu4_border_loop_head_install()) {
        return false;
    }
    return eu4_border_loop_head_hook_installed();
}

bool eu4_submission_hooks_are_installed(void) {
    return eu4_border_loop_head_hook_installed();
}
