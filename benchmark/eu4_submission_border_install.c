#include <stdbool.h>

#include "eu4_submission_border.h"

bool eu4_submission_try_install_all_hooks(void) {
    eu4_border_init_gl_apis();
    return eu4_border_gl_api_ready();
}

bool eu4_submission_hooks_are_installed(void) {
    return eu4_border_gl_api_ready();
}
