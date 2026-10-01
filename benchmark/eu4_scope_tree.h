#ifndef EU4_SCOPE_TREE_H
#define EU4_SCOPE_TREE_H
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define EU4_SCOPE_DEPTH 64
#define EU4_SCOPE_NODES 128
#define EU4_SCOPE_OVERFLOW 512u
#define EU4_SCOPE_INVALID 1024u
typedef struct {
    int parent; unsigned id;
    uint64_t calls,inclusive_wall,inclusive_cpu,exclusive_wall,exclusive_cpu;
} Eu4ScopeNode;
typedef struct { unsigned node; uint64_t wall,cpu,child_wall,child_cpu; } Eu4ScopeEntry;
typedef struct {
    Eu4ScopeNode nodes[EU4_SCOPE_NODES];
    Eu4ScopeEntry stack[EU4_SCOPE_DEPTH];
    unsigned count,depth,flags;
} Eu4ScopeTree;
typedef struct { Eu4ScopeNode nodes[EU4_SCOPE_NODES]; unsigned count,flags; } Eu4ScopeSnapshot;
static inline void eu4_scope_snapshot(Eu4ScopeSnapshot *out,const Eu4ScopeTree *tree) {
    out->count=tree->count; out->flags=tree->flags|(tree->depth?EU4_SCOPE_INVALID:0);
    memcpy(out->nodes,tree->nodes,tree->count*sizeof(tree->nodes[0]));
}
static inline int eu4_scope_begin(Eu4ScopeTree *tree,unsigned id,bool enabled,
                                  uint64_t wall,uint64_t cpu) {
    if(!enabled) return 0;
    if(tree->depth>=EU4_SCOPE_DEPTH) { tree->flags|=EU4_SCOPE_OVERFLOW; return 0; }
    int parent=tree->depth?(int)tree->stack[tree->depth-1].node:-1;
    unsigned node=0;
    while(node<tree->count && (tree->nodes[node].parent!=parent || tree->nodes[node].id!=id)) node++;
    if(node==tree->count) {
        if(node==EU4_SCOPE_NODES) { tree->flags|=EU4_SCOPE_OVERFLOW; return 0; }
        tree->nodes[node]=(Eu4ScopeNode){.parent=parent,.id=id}; tree->count++;
    }
    tree->stack[tree->depth++]=(Eu4ScopeEntry){.node=node,.wall=wall,.cpu=cpu};
    return (int)tree->depth;
}
static inline void eu4_scope_end(Eu4ScopeTree *tree,unsigned id,int token,
                                 uint64_t wall,uint64_t cpu) {
    if(!token) return;
    if((unsigned)token!=tree->depth || !tree->depth ||
       tree->nodes[tree->stack[tree->depth-1].node].id!=id) {
        tree->flags|=EU4_SCOPE_INVALID; tree->depth=0; return;
    }
    Eu4ScopeEntry entry=tree->stack[--tree->depth];
    if(wall<entry.wall || cpu<entry.cpu || wall-entry.wall<entry.child_wall ||
       cpu-entry.cpu<entry.child_cpu) { tree->flags|=EU4_SCOPE_INVALID; return; }
    uint64_t dw=wall-entry.wall,dc=cpu-entry.cpu;
    Eu4ScopeNode *node=&tree->nodes[entry.node];
    node->calls++; node->inclusive_wall+=dw; node->inclusive_cpu+=dc;
    node->exclusive_wall+=dw-entry.child_wall; node->exclusive_cpu+=dc-entry.child_cpu;
    if(tree->depth) {
        tree->stack[tree->depth-1].child_wall+=dw;
        tree->stack[tree->depth-1].child_cpu+=dc;
    }
}
/* Q v2: update,phase,parent-node,scope,calls,in-wall,in-cpu,ex-wall,ex-cpu,epoch,node,flags. */
static inline int eu4_scope_record(char *line,size_t size,const Eu4ScopeTree *tree,
                                  unsigned index,uint64_t update,uint64_t phase,uint64_t epoch) {
    const Eu4ScopeNode *n=&tree->nodes[index];
    return snprintf(line,size,"Q,%llu,%llu,%d,%u,%llu,%llu,%llu,%llu,%llu,%llu,%u,%u\n",
        (unsigned long long)update,(unsigned long long)phase,n->parent,n->id,
        (unsigned long long)n->calls,(unsigned long long)n->inclusive_wall,
        (unsigned long long)n->inclusive_cpu,(unsigned long long)n->exclusive_wall,
        (unsigned long long)n->exclusive_cpu,(unsigned long long)epoch,index,
        tree->flags | (tree->depth?EU4_SCOPE_INVALID:0));
}
#endif
