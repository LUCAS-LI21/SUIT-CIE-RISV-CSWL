obj-m := camclk.o

KDIR := /lib/modules/$(shell uname -r)/build
PWD  := $(shell pwd)

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean

install:
	insmod ./camclk.ko

uninstall:
	rmmod camclk

.PHONY: all clean install uninstall
