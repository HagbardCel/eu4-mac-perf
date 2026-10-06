#include <stdbool.h>
#include <stdlib.h>

#include "eu4_submission_border.h"
#include "eu4_submission_border_loop_head.h"

bool eu4_submission_try_install_all_hooks(void) {
    eu4_border_loop_head_install_result_t result = eu4_border_loop_head_install_ex();
    if (result == EU4_BORDER_LOOP_HEAD_INSTALL_FAILED_FATAL) {
        abort();
    }
    if (result == EU4_BORDER_LOOP_HEAD_INSTALL_OK) {
        return eu4_border_loop_head_hook_installed();
    }
    return false;
}

bool eu4_submission_hooks_are_installed(void) {
    return eu4_border_loop_head_hook_installed();
}
