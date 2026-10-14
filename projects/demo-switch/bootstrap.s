/* Original minimal module metadata, CC0-1.0. Not a console runtime. */
.section .text.bootstrap,"ax"
.global _module_start
_module_start:
    b _start
    .word _mod0 - _module_start
.section .rodata.mod0,"a"
_mod0:
    .ascii "MOD0"
    .word _dynamic - _mod0
    .word __bss_start - _mod0
    .word __bss_end - _mod0
    .word 0, 0, 0, 0
.balign 8
_dynstr:
    .asciz ""
    .asciz "_start"
    .asciz "clamp_value"
    .asciz "checksum"
_dynstr_end:
.balign 8
_dynsym:
    .zero 24
    .word 1
    .byte 0x12, 0
    .short 1
    .quad _start, 0
    .word 8
    .byte 0x12, 0
    .short 1
    .quad clamp_value, 0
    .word 20
    .byte 0x12, 0
    .short 1
    .quad checksum, 0
_hash:
    .word 1, 4, 1, 0, 2, 3, 0
.section .data.dynamic,"aw"
_dynamic:
    .quad 5, _dynstr
    .quad 10, _dynstr_end - _dynstr
    .quad 6, _dynsym
    .quad 11, 24
    .quad 4, _hash
    .quad 0, 0
