################################################################################
#
# vvcam
#
################################################################################

VVCAM_DIR_NAME := vvcam
VVCAM_APP_NAME := vvcam
VVCAM_SITE = $(realpath $(TOPDIR))"/package/vvcam"
VVCAM_SITE_METHOD = local
VVCAM_INSTALL_STAGING = YES
VVCAM_INSTALL_TARGET = YES
VVCAM_DEPENDENCIES = mxml libmicrohttpd display umtprd  android-tools
VVCAM_SUPPORTS_IN_SOURCE_BUILD = NO

VVCAM_MODULE_MAKE_OPTS += BR2_PACKAGE_VVCAM_DEF_SENSOR=$(BR2_PACKAGE_VVCAM_DEF_SENSOR)

$(eval $(kernel-module))

VVCAM_DEB_ROOT = $(@D)/deb-package

ifeq ($(BR2_PACKAGE_K230_CAMERA),y)
define VVCAM_DEB_CAMERA_FILES
	rm -f $(VVCAM_DEB_ROOT)/etc/systemd/system/vvcam.service $(VVCAM_DEB_ROOT)/etc/vvcam/isp_start.sh
	$(INSTALL) -m 0755 $(VVCAM_PKGDIR)/deb-camera/DEBIAN/post* -t $(VVCAM_DEB_ROOT)/DEBIAN
	$(INSTALL) -m 0644 $(VVCAM_PKGDIR)/deb-camera/etc/systemd/system/*.service -t $(VVCAM_DEB_ROOT)/etc/systemd/system
	$(INSTALL) -m 0755 $(VVCAM_PKGDIR)/deb-camera/etc/vvcam/*.sh -t $(VVCAM_DEB_ROOT)/etc/vvcam
	sed -i 's/^Version:.*/Version: 1.1/' $(VVCAM_DEB_ROOT)/DEBIAN/control
endef
endif

define VVCAM_BUILD_DEB
	rm -rf $(VVCAM_DEB_ROOT)
	cp -a $(VVCAM_PKGDIR)/deb $(VVCAM_DEB_ROOT)
	$(call COPYFILE ,$(TARGET_DIR)/etc/vvcam,$(VVCAM_DEB_ROOT)/etc/)
	$(call COPYFILE ,$(TARGET_DIR)/usr/lib/libvvcam.so,$(VVCAM_DEB_ROOT)/usr/lib/riscv64-linux-gnu/)
	$(call COPYFILE ,$(TARGET_DIR)/usr/lib/libv4l2-drm.so,$(VVCAM_DEB_ROOT)/usr/lib/riscv64-linux-gnu/)
	$(call COPYFILE ,$(TARGET_DIR)/usr/lib/libdisplay.so,$(VVCAM_DEB_ROOT)/usr/lib/riscv64-linux-gnu/)
	$(call COPYFILE ,$(@D)/isp_media_server_debian,$(VVCAM_DEB_ROOT)/usr/bin/)
	$(call COPYFILE ,$(TARGET_DIR)/usr/bin/v4l2-drm,$(VVCAM_DEB_ROOT)/usr/bin/)
	$(call COPYFILE ,$(TARGET_DIR)/lib/modules/6.6.36/updates/,$(VVCAM_DEB_ROOT)/usr/lib/modules/6.6.36/)
	$(INSTALL) -m 0755 $(BR2_ROOTFS_OVERLAY)/etc/init.d/S41adb_mtp $(VVCAM_DEB_ROOT)/etc/vvcam/
	$(call COPYFILE ,$(BR2_ROOTFS_OVERLAY)/etc/umtprd,$(VVCAM_DEB_ROOT)/etc/)
	$(call COPYFILE ,$(TARGET_DIR)/usr/sbin/umtprd,$(VVCAM_DEB_ROOT)/usr/sbin/)
	$(call COPYFILE ,$(TARGET_DIR)/usr/bin/adbd,$(VVCAM_DEB_ROOT)/usr/bin/)
	$(call COPYFILE ,$(TARGET_DIR)/usr/lib/libcrypt.so.2,$(VVCAM_DEB_ROOT)/usr/lib/riscv64-linux-gnu/)
	$(VVCAM_DEB_CAMERA_FILES)
	dpkg-deb --build --root-owner-group $(VVCAM_DEB_ROOT) $(BINARIES_DIR)/deb/$(call LOWERCASE, k230-$(PKG)).deb
endef

VVCAM_POST_INSTALL_TARGET_HOOKS += VVCAM_BUILD_DEB

$(eval $(cmake-package))
