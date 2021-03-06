# 条件随机场
# 实现CRF的训练和使用，语料的预处理，标注结果的评估

import copy
import numpy as np
import random
import itertools
import pickle
from multiprocessing import Pool
import sys

class ShowProcess():
    """
    显示处理进度的类
    调用该类相关函数即可实现处理进度的显示
    """
    i = 0 # 当前的处理进度
    max_steps = 0 # 总共需要处理的次数
    max_arrow = 50 #进度条的长度
    infoDone = 'done'

    # 初始化函数，需要知道总共的处理次数
    def __init__(self, max_steps, infoDone = 'Done'):
        self.max_steps = max_steps
        self.i = 0
        self.infoDone = infoDone

    # 显示函数，根据当前的处理进度i显示进度
    # 效果为[>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>]100.00%
    def show_process(self, i=None):
        if i is not None:
            self.i = i
        else:
            self.i += 1
        num_arrow = int(self.i * self.max_arrow / self.max_steps) #计算显示多少个'>'
        num_line = self.max_arrow - num_arrow #计算显示多少个'-'
        percent = self.i * 100.0 / self.max_steps #计算完成进度，格式为xx.xx%
        process_bar = '[' + '>' * num_arrow + '-' * num_line + ']'\
                      + '%.2f' % percent + '%' + '\r' #带输出的字符串，'\r'表示不换行回到最左边
        sys.stdout.write(process_bar) #这两句打印字符到终端
        sys.stdout.flush()
        if self.i >= self.max_steps:
            self.close()

    def close(self):
        print('')
        print(self.infoDone)
        self.i = 0

class LinearChainCRF():

    def __init__(self, learningRate=0.001, epoch=1, workerNum=4):
        self.epoch = epoch
        self.workerNum = workerNum
        # 定义CRF的参数
        # 特征模板。如果没有设置，就是用使用默认的线性链条件随机场的模板规则，即只考虑当前
        # 观测值x_t，当前隐藏状态y_t，以及前一个隐藏状态y_before_t
        self.hiddenStatList = None  # 隐藏状态的取值空间
        self.minWordNum = None  # 语料中的词语，出现次数小于这个阈值的，不会用于特征模板
        self.minCharNum = None  # 语料中的字符，出现次数小于这个阈值的，不会用于模板
        self.gradListOfWeights = None  # 训练过程中，存储各个模板函数权重的梯度
        self.learningRate = learningRate  # 模型训练的学习率，这里为了简单，使用一个统一的
        self.hiddenStateNum = None
        self.featureWeightMap = None  # 每一个模板函数的权重,为了减小激素啊过程的内存消耗，这里使用map存储
        self.stateTransFeatureSet = None
        self.stateFeatureSet = None
        self.featureFunctionNum = None  # 特征函数的个数
        # 为了简单，这里暂时不加正则化

    # 判断当前状态转换是否符合某一个特征模板，返回值为这个特征函数的取值
    def ifFitFeatureTemplet(self, statTransFeature):
        if statTransFeature in self.featureWeightMap:
            return 1.
        else:
            return 0.

    # 基于超参数和训练数据，初始化CRF的参数
    def initParamWithTraingData(self, traningData):
        self.featureWeightMap = {}
        stateTransFeatureNumMap, statFeatureNumMap = {}, {}
        self.stateTransFeatureSet, self.stateFeatureSet = None, None
        hiddenStateSet = set({})
        for sentence in traningData:
            charList = '@' + sentence[0] + '@'
            tagList = '*' + sentence[1] + '#'
            for tag in tagList:
                hiddenStateSet.add(tag)
            sentenceLength = len(charList)
            for i in range(1, sentenceLength):
                statTransFeature = tagList[
                                   i - 1: i + 1]  # 线性链CRF的特征函数只有两种
                statFeature = tagList[i] + charList[i]
                # 统计特征在语料中出现的次数，如果太低，就删掉
                stateTransFeatureNumMap[statTransFeature] = stateTransFeatureNumMap.get(statTransFeature, 0) + 1
                statFeatureNumMap[statFeature] = statFeatureNumMap.get(statFeature, 0) + 1
        for feature in list(stateTransFeatureNumMap.keys()):
            if stateTransFeatureNumMap[feature] < 1:
                del stateTransFeatureNumMap[feature]
            else:  # 出现次数较高的特征，给一个初始权重
                self.featureWeightMap[feature] = 0*random.uniform(-0.1, 0.1)
        for feature in list(statFeatureNumMap.keys()):
            if statFeatureNumMap[feature] < 1:
                del statFeatureNumMap[feature]
            else:  # 出现次数较高的特征，给一个初始权重
                self.featureWeightMap[feature] = 0 * random.uniform(-0.1, 0.1)
        self.stateTransFeatureSet = set(list(stateTransFeatureNumMap.keys()))
        self.stateFeatureSet = set(list(statFeatureNumMap.keys()))
        #         print("特征函数的初始权重是", self.featureWeightMap)
        featureNameList = list(self.featureWeightMap.keys())
        self.featureFunctionNum = len(featureNameList)
        hiddenStateSet.remove('*')
        hiddenStateSet.remove("#")
        self.hiddenStatList = list(hiddenStateSet)
        self.hiddenStateNum = len(hiddenStateSet)
        print("特征函数的个数是", self.featureFunctionNum)

    # 计算观测序列t处，某个特征函数的取值，以及对应的边缘概率，为计算梯度做准备
    def calFeatureFunctionValueAndMargProb(self, featureName, observationList, t):
        observation_t = observationList[t]
        state_t = featureName[1]
        stateFormer = featureName[0]
        margProb = self.forwardAlgrithm(stateFormer, observationList, t - 1) * \
                    np.exp(self.getSumOfFeatureFuctions(state_t, stateFormer, observation_t)) * \
                   self.backwardAlgrithm(state_t, observationList, t)
        return margProb

    # 已知模型参数，求一个观测值处的特征函数加权和
    def getSumOfFeatureFuctions(self, thisState, formerState, thisObservation):
        stateTrans = formerState + thisState
        stateFeature = thisState + thisObservation
        featureValue = self.featureWeightMap.get(stateTrans, 0) * \
                       self.ifFitFeatureTemplet(stateTrans) +     \
                       self.featureWeightMap.get(stateFeature, 0) *  \
                       self.ifFitFeatureTemplet(stateFeature)
        return featureValue

    # 已知CRF模型参数和一个观测序列x=(x_1, x_2, ..., x_T)，
    # 求x_t处的前向变量取值aplha_t(thisState)
    def forwardAlgrithm(self, state_t, observationList, t):
        alphaList = []  # 存储前向向量
        alphaList.append([
                             1])  # 认为添加的start步，也就是t=0的位置，对应的状态只有一个，前向向量
        # 的长度就是1,特征函数的个数是0,向量元素的取值就是exp(0)=1
        for i in range(1, t):
            thisObservation = observationList[i]
            thisAlpha = np.zeros(self.hiddenStateNum)  # t>=1时，隐藏状态的个数
            formerAlpha = alphaList[-1]
            stateNumOfFormerStep = len(formerAlpha)
            transProbMatrix = np.zeros((len(formerAlpha),
                                        self.hiddenStateNum))  # 用于存储t-1步的隐藏状态到t步
            # 隐藏状态的非规范化转移概率
            for n in range(
                    len(self.hiddenStatList)):  # 遍历本步的所有隐藏状态
                thisState = self.hiddenStatList[n]
                for j in range(stateNumOfFormerStep):
                    formerState = self.hiddenStatList[j]
                    featureFunctionSum = self.getSumOfFeatureFuctions(thisState, formerState, thisObservation)
                    transProbMatrix[j, n] = featureFunctionSum
            transProbMatrix = np.exp(transProbMatrix)
            thisAlpha = np.dot(formerAlpha, transProbMatrix)
            alphaList.append(thisAlpha)

        thisState = state_t
        thisObservation = observationList[t]
        formerAlpha = alphaList[-1]
        stateNumOfFormerStep = len(formerAlpha)
        alpha_t = 0  # x_t处，隐藏状态取值为state_t时的前向变量取值
        for j in range(stateNumOfFormerStep):
            formerState = self.hiddenStatList[j]
            featureFunctionSum = self.getSumOfFeatureFuctions(thisState, formerState, thisObservation)
            alpha_t += np.exp(featureFunctionSum) * formerAlpha[j]
        return alpha_t

    # 已知CRF模型参数和一个观测序列x=(x_1, x_2, ..., x_T)，用后向算法
    # 求x_t处的条件概率p(y_t=s, y_t-1=s_dot|x)
    def backwardAlgrithm(self, state_t, observationList, t):
        betaList = [None for _ in range(len(observationList))]  # 存储前向向量
        betaList[-1] = [1]  # 认为添加的start步，也就是t=0的位置，对应的状态只有一个，前向向量
        # 的长度就是1,特征函数的个数是0,向量元素的取值就是exp(0)=1
        for i in range(len(observationList) - 2, t, -1):
            thisObservation = observationList[i]
            formerBeta = betaList[i + 1]
            stateNumOfFormerStep = len(formerBeta)
            transProbMatrix = np.zeros((len(formerBeta), self.hiddenStateNum))
            for n in range(len(self.hiddenStatList)):  # 遍历本步的所有隐藏状态
                thisState = self.hiddenStatList[n]
                for j in range(stateNumOfFormerStep):
                    laterState = self.hiddenStatList[j]
                    featureFunctionSum = self.getSumOfFeatureFuctions(laterState, thisState, thisObservation)
                    transProbMatrix[j, n] = featureFunctionSum
            transProbMatrix = np.exp(transProbMatrix)
            thisBeta = np.dot(formerBeta, transProbMatrix)
            betaList[i] = thisBeta

        thisState = state_t
        laterObservation = observationList[t + 1]
        #         print(t, len(betaList), betaList)
        laterBeta = betaList[t + 1]
        stateNumOfFormerStep = len(laterBeta)
        beta_t = 0  # x_t处，隐藏状态取值为state_t时的前向变量取值
        for j in range(stateNumOfFormerStep):
            laterState = self.hiddenStatList[j]
            featureFunctionSum = self.getSumOfFeatureFuctions(thisState, laterState, laterObservation)
            beta_t += np.exp(featureFunctionSum) * laterBeta[j]
        return beta_t

    def generatePossibleStateTrans(self,t, stateList, observationList):  # 生成第t步，可能的隐藏状态转移
        if t == 1:
            featureNames = itertools.product(['*'], self.hiddenStatList)
        elif t==len(stateList)-1:
            featureNames = itertools.product(self.hiddenStatList, ['#'])
        else:
            featureNames = itertools.product(self.hiddenStatList, self.hiddenStatList)
        featureNames = list(map(lambda x: ''.join(x), featureNames))
        featureNames = list(filter(lambda x: x in self.featureWeightMap, featureNames))
        return featureNames

    def generatePossibleStateFeatueNames(self, t, observation):
        featureNames = itertools.product(self.hiddenStatList, [observation])
        featureNames = list(map(lambda x: ''.join(x), featureNames))
        featureNames = list(filter(lambda x: x in self.featureWeightMap, featureNames))
        return featureNames

    # 基于更新规则更新权重
    def updateWeight(self, gradMap):
        for featureName in gradMap:
            if featureName in self.featureWeightMap:
                self.featureWeightMap[featureName] += self.learningRate * gradMap[featureName]

    # 基于训练语料，估计CRF参数
    def fitMulti(self, sentenceList):
        if self.preTrain == False:
            self.initParamWithTraingData(sentenceList)
        batchSize = self.workerNum
        miniBatchSize = 1#self.workerNum
        corpusSize = len(sentenceList)
        weightList = []
        initLearningRate = float(self.learningRate)
        weight1, weight2 = 0, 0

        for epoch in range(self.epoch):
            
            random.shuffle(sentenceList)
            for n in range(0, len(sentenceList), batchSize):
                t1 = time.time()
                dataBatch = sentenceList[n: n+batchSize]
                pool = Pool(self.workerNum)
                gradsList = []
                pickle.dump(self, open('md.pkl', 'wb'))
                for j in range(0, batchSize, miniBatchSize):
                    miniBatch = dataBatch[j:j+miniBatchSize]
                    weight1 = self.featureWeightMap['ES']
                    result = pool.apply_async(calGrad4WeightSlowMultiProcessNew, args=(self, miniBatch, batchSize, epoch, n))
                    gradsList.append(result)
                pool.close()
                pool.join()
                print("开始本轮的梯度计算和权重更新。")
                for result in gradsList:
                    gradMap = result.get()
                    self.learningRate = initLearningRate / (2 * (1 + epoch))
                    self.updateWeight(gradMap)  # 基于更新规则更新权重
                    weight2 = self.featureWeightMap['ES']
                cost = self.calCost(sentenceList[:batchSize])
                t2 = time.time()
                print("epoch:", epoch, ',sentence', n, ',cost:', cost, ",weight of 'ES':", self.featureWeightMap['ES'], ',time cost is', t2-t1)
  
            print(model.predict(sentenceList[0][0]))
            weightList.append(self.featureWeightMap['ES'])
            if np.isnan(self.featureWeightMap['ES']) == True or np.abs(weight2 - weight1) > 10:
                print(np.isnan(self.featureWeightMap['ES']))
                break

#         from matplotlib import pyplot as plt
#         plt.plot(weightList)
#         plt.show()

    def calCost(self, sentenceList):
        cost = 0
        for sentence in sentenceList:
            charList = '@' + sentence[0] + '@'
            tagList = '*' + sentence[1] + '#'
            for t in range(1, len(charList) - 1):
                thisState = tagList[t]
                formerState = tagList[t - 1]
                thisObservation = charList[t]
                cost += self.getSumOfFeatureFuctions(thisState, formerState, thisObservation)
            z_x = self.backwardAlgrithm('*', charList, 0)  # 计算配分函数的取值
            cost -= np.log(z_x)
        return cost

    # 基于观测值序列，也就是语句话的字符串列表，使用模型选出最好的隐藏状态序列，并按照分词标记将字符聚合成分词结果
    def predict(self, text):
        statPathProbMap = {}  # 存储以各个初始状态打头的概率最大stat路径
        for stat in self.hiddenStatList:  # 遍历每一个隐藏状态
            statPath = stat  # 这是目前积累到的stat路径，也就是分词标记序列
            statPathProb = self.getSumOfFeatureFuctions(stat, '*', text[0])
            statPathProbMapOfThis = {}
            statPathProbMapOfThis[statPath] = statPathProb
            for t in range(1, len(text)):
                char = text[t]
                tempPathProbMap = {}
                for statValue in self.hiddenStatList:
                    thisState = statValue
                    formerState = statPath[-1]
                    tempPath = statPath + thisState
                    tempPathProb = self.getSumOfFeatureFuctions(thisState, formerState, char)
                    tempPathProbMap[tempPath] = tempPathProb
                bestPath = getKeyWithMaxValueInMap(tempPathProbMap)
                statPathProbMapOfThis[bestPath] = tempPathProbMap[bestPath]
                statPath = bestPath
            statPathProbMap[statPath] = statPathProbMapOfThis[statPath]
        bestPath = getKeyWithMaxValueInMap(statPathProbMap)
        print(text)
        res = mergeCharsInOneWord(text, bestPath)
        return res

    def setMode(self, preTrain=True):
        self.preTrain = preTrain


def getKeyWithMaxValueInMap(dataMap):
    dataList = sorted(dataMap.items(), key=lambda x: x[1], reverse=True)
    theKey = dataList[0][0]
    return theKey


# 基于分词标记把字符聚合起来，形成分词结果
def mergeCharsInOneWord(charList, tagList):
    wordList = []
    word = ''
    for i in range(len(charList)):
        tag, char = tagList[i], charList[i]
        if tag == 'E':
            word += char
            wordList.append(word)
            word = ''
        elif tag == "S":
            word += char
            wordList.append(word)
            word = ''
        else:
            word += char
    return wordList


def loadData(fileName, sentenceNum=100):
    with open(fileName, 'r', encoding='utf8') as f:
        line = f.readline()
        corpus = []
        tempSentence = []
        tempTag = []
        count = 0
        while line != True:
            line = line.replace('', '').replace('\n', '')
            if line == '':  # 如果这一行没有字符，说明到了句子的末尾
                tempSentence = ''.join(
                    tempSentence)  # 把字符都连接起来形成字符串，后面操作的时候会快一些
                #                 if "习近平" in tempSentence:
                #                     print(tempSentence)
                tempTag = ''.join(tempTag)
                corpus.append([tempSentence[:100], tempTag[:100]])
                #                 corpus.append([tempSentence[:20],tempTag[:20]])
                #                 print("这句话是", [tempSentence,tempTag])
                tempSentence = []
                tempTag = []
                count += 1
                if count == sentenceNum:  # 如果积累的句子个数达到阈值，返回语料
                    return corpus
            else:
                line = line.split('	')
                # print(line)
                [char, tag] = line[0], line[2]  # 取出语料的文字和分词标记
                tempSentence.append(char)
                tempTag.append(tag)
            line = f.readline()
    return corpus


def calGrad4WeightSlowMultiProcess(self, dataBatch, corpusSize, epoch, n):
#     print("第", epoch, '轮，进度是', n, '/', corpusSize)
    gradMap = {}
    # process_bar = ShowProcess(len(dataBatch), 'OK')
    for sentence in dataBatch:
        #     print("这是第", n, '句。')
        charList = '@' + sentence[0] + '@'
        tagList = '*' + sentence[1] + '#'
        sentenceLength = len(charList)
        z_x = 0
        for state in self.stateFeatureSet:
            z_x += self.backwardAlgrithm(state, charList, 1)  # 计算配分函数的取值
        gradMap = {}
    
        regFenmu = corpusSize * 10
        for featureName in self.featureWeightMap:
            grad = 0
            for t in range(1, sentenceLength - 1):
                thisState, formerState = tagList[t], tagList[t - 1]
                thisObservation = charList[t]
                featureName2 = formerState + thisState
                featureName1 = thisState + thisObservation
                if featureName in [featureName2, featureName1]:
                    grad += 1
                possibleFeatureNames = self.generatePossibleStateFeatueNames(t, thisObservation) + \
                                       self.generatePossibleStateTrans(t, tagList, charList)
                if featureName in possibleFeatureNames:
                    grad = grad - self.calFeatureFunctionValueAndMargProb(featureName, charList, t) / z_x
            gradMap[featureName] = grad
        for key in gradMap:
            if key in self.featureWeightMap:
                gradMap[key] -= self.featureWeightMap.get(key, 0) / regFenmu
    # print(gradMap)
    return gradMap


def calGrad4WeightSlowMultiProcessNew(self, dataBatch, corpusSize, epoch, n):
#     print("第", epoch, '轮，进度是', n, '/', corpusSize)
    gradMap = {}
    # process_bar = ShowProcess(len(dataBatch), 'OK')
    for sentence in dataBatch:
        # process_bar.show_process()
        charList = '@' + sentence[0] + '@'
        tagList = '*' + sentence[1] + '#'
        sentenceLength = len(charList)
        z_x = 0
        for state in self.stateFeatureSet:
            z_x += self.backwardAlgrithm(state, charList, 1)  # 计算配分函数的取值
        regFenmu = corpusSize * 10
        for t in range(1, sentenceLength-1):
            thisState, formerState = tagList[t], tagList[t - 1]
            thisObservation = charList[t]
            featureName2 = formerState + thisState
            featureName1 = thisState + thisObservation
            gradMap[featureName2] = gradMap.get(featureName2, 0) + self.ifFitFeatureTemplet(featureName2)
            gradMap[featureName1] = gradMap.get(featureName1, 0) + self.ifFitFeatureTemplet(featureName1)
            possibleFeatureNames = self.generatePossibleStateFeatueNames(t, thisObservation) + \
                                   self.generatePossibleStateTrans(t, tagList, charList)
            for featureName in possibleFeatureNames:
                gradMap[featureName] = gradMap.get(featureName, 0) -\
                      self.calFeatureFunctionValueAndMargProb(featureName, charList, t) / z_x

                # print('asdasd', np.exp(self.calFeatureFunctionValueAndMargProb(featureName, charList, t)) / z_x)
        for key in gradMap:
            if key in self.featureWeightMap:
                gradMap[key] -= self.featureWeightMap.get(key, 0) / regFenmu
        # print(gradMap)
    return gradMap


import time

if __name__ == '__main__':
    fileName = r"msra_training.txt"
    sentenceNum = 20
    sentenceList = loadData(fileName, sentenceNum=sentenceNum)  # 加载语料
    #     print(sentenceList)
    preTrain = False  # False#,True
    if preTrain:
        model = pickle.load(open('md.pkl', 'rb'))
        model.setMode(preTrain=True)
        model.learningRate = 0.001
    else:
        model = LinearChainCRF(epoch=500, learningRate=0.005, workerNum=1)
        model.setMode(preTrain=False)

    model.fitMulti(sentenceList)
    random.shuffle(sentenceList)
    pickle.dump(model, open('md.pkl', 'wb'))
    for line in sentenceList[:10]:
        res = model.predict(line[0])
        print("分词结果是", res)
    #, "真实的分词结果是",  mergeCharsInOneWord(line[0]), line[1]))
    #
    # s = "我是一个粉刷将，粉刷本领强。我要把我的新房子刷的很漂亮。"
    # res = model.predict(s)
    # testS = ['我是一个粉刷将，粉刷本领强。', '我要把我的新房子刷的很漂亮。',
    #           '我是一个粉刷将，粉刷本领强。我要把我的新房子刷的很漂亮。',
    #           '习近平指出，当前我国社会的主要矛盾仍然是人民日益增长的物质需求与不发达的生产力之间的矛盾。']
    # for s in testS:
    #     t1 = time.time()
    #     res = model.predict(s)
    #     t2 = time.time()
    #     print(t2-t1, res)
