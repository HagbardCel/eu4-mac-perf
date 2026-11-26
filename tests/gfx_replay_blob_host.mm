/* Host for the production repeated-blob reference verifier.

   The in-process shadow bridge verifies every blob reference in the sealed
   event stream before executing Metal.  A content-addressed blob may be
   referenced by two records with different metadata completeness (one omits
   "bytes"); that is not a conflict unless the declared sizes disagree.  This
   host exercises the verifier directly, without a Metal device or a replay. */

#import <Foundation/Foundation.h>

#include "../renderer/metal/eu4_gfx_metal_replay.h"

#include <stdio.h>

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <frame-dir>\n", argv[0]);
        return 2;
    }
    int result = eu4_gfx_frame_replay_test_blob_references(argv[1]);
    printf("blob_references=%d\n", result);
    return result == 0 ? 0 : 1;
}
