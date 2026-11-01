import { defineStore } from 'pinia'
import { reactive } from 'vue'

export interface Profile {
  name: string
  major: string
  grade: string
  contact: string
}

export interface Experience {
  title: string
  start: string
  end: string
  description: string
  duty: string
}

export interface ResumeState {
  profile: Profile
  skills: string[]
  experiences: Experience[]
}

const initialState: ResumeState = {
  profile: {
    name: '张三',
    major: '计算机科学与技术',
    grade: '大三',
    contact: '138 0000 0000',
  },
  skills: ['Python', 'SQL', 'Vue 3'],
  experiences: [
    {
      title: '校园二手交易平台',
      start: '2024.03',
      end: '2024.06',
      description: '面向校内学生的二手交易 Web 应用。',
      duty: '负责后端 API 与数据库设计。',
    },
  ],
}

export const useResumeStore = defineStore('resume', () => {
  const resume = reactive<ResumeState>(structuredClone(initialState))

  function updateProfile<K extends keyof Profile>(field: K, value: Profile[K]) {
    resume.profile[field] = value
  }

  function updateSkill(index: number, value: string) {
    if (resume.skills[index] !== undefined) resume.skills[index] = value
  }

  function addSkill() {
    resume.skills.push('')
  }

  function removeSkill(index: number) {
    resume.skills.splice(index, 1)
  }

  function updateExperience(index: number, field: keyof Experience, value: string) {
    const experience = resume.experiences[index]
    if (experience) experience[field] = value
  }

  function addExperience() {
    resume.experiences.push({
      title: '',
      start: '',
      end: '',
      description: '',
      duty: '',
    })
  }

  function removeExperience(index: number) {
    resume.experiences.splice(index, 1)
  }

  return {
    resume,
    updateProfile,
    updateSkill,
    addSkill,
    removeSkill,
    updateExperience,
    addExperience,
    removeExperience,
  }
})
