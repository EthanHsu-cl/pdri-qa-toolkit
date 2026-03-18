pipeline {
    agent { label 'mac-mini-m1' }
    parameters {
        choice(name: 'TEST_SCOPE', choices: ['q4', 'q4,q3', 'q4,q3,q2', 'q4,q3,q2,q1', 'visual'], description: 'Test quadrants to run')
        string(name: 'BUILD_NUM', defaultValue: '', description: 'Build number')
    }
    environment {
        APPIUM_URL = 'http://127.0.0.1:4723'
        BUNDLE_ID  = 'com.cyberlink.powerdirector'
        DEVICE_NAME = 'iPhone'
        BASELINE_DIR = 'visual_baselines'
        RESULT_DIR   = 'visual_results'
    }
    stages {
        stage('Setup') {
            steps {
                sh 'python3 -m pip install -r requirements.txt --quiet'
                sh 'mkdir -p visual_baselines visual_results data'
            }
        }
        stage('Q4 - Critical') {
            when { expression { params.TEST_SCOPE.contains('q4') } }
            steps {
                sh 'python3 -m pytest tests/ -m q4 --alluredir=allure-results/q4 -v --tb=short || true'
            }
        }
        stage('Q3 - Important') {
            when { expression { params.TEST_SCOPE.contains('q3') } }
            steps {
                sh 'python3 -m pytest tests/ -m q3 --alluredir=allure-results/q3 -v --tb=short || true'
            }
        }
        stage('Q2 - Standard') {
            when { expression { params.TEST_SCOPE.contains('q2') } }
            steps {
                sh 'python3 -m pytest tests/ -m q2 --alluredir=allure-results/q2 -v --tb=short || true'
            }
        }
        stage('Q1 - Low Risk') {
            when { expression { params.TEST_SCOPE.contains('q1') } }
            steps {
                sh 'python3 -m pytest tests/ -m q1 --alluredir=allure-results/q1 -v --tb=short || true'
            }
        }
        stage('Visual Regression') {
            when { expression { params.TEST_SCOPE.contains('visual') } }
            steps {
                sh 'python3 -m pytest tests/ -m visual --alluredir=allure-results/visual -v --tb=short || true'
            }
        }
    }
    post {
        always {
            allure includeProperties: false, jdk: '', results: [[path: 'allure-results']]
            archiveArtifacts artifacts: 'visual_results/**', allowEmptyArchive: true
        }
        failure { echo 'Tests failed - check Allure report' }
    }
}
