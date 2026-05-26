<template>
	<ion-page>
		<ion-content class="ion-padding">
			<div class="flex h-screen w-screen flex-col justify-center bg-white">
				<Dialog
					:options="{
						title: '登录失败',
						message: '未找到与邮箱 ' + session?.user + ' 关联的在职员工。请使用员工邮箱登录，或联系HR经理获取访问权限。',
						size: 'lg',
						actions: [
							{
								label: '前往登录',
								variant: 'solid',
								onClick: () => session.logout.submit(),
							},
						],
					}"
					v-model="showDialog"
					@close="
						() => {
							session.logout.submit()
							showDialog = false
						}
					"
				/>
			</div>
		</ion-content>
	</ion-page>
</template>

<script setup>
import { IonPage, IonContent } from "@ionic/vue"
import { inject, ref } from "vue"
import { Dialog } from "frappe-ui"

const session = inject("$session")
const showDialog = ref(true)
</script>
